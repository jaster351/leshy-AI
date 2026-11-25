"""Build local, explicitly selected synthetic LiveMentor training data.

No personal conversation store, network, model loader or training side effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .mentor_batch import digest, encode, read_obj, read_bytes
from .mentor_feedback_view import committed_reviews
from .mentor_context import SYSTEM

_BATCH = re.compile(r"mentor-batch-[a-z0-9-]{1,55}")
_QID = re.compile(r"q[0-9]{1,6}")
_SECRET = re.compile(
    r"(?i)(?:gh[pousr]_[a-z0-9_]{12,}|github_pat_[a-z0-9_]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[a-z0-9_-]{16,}|"
    r"bearer\s+[a-z0-9_.-]{12,}|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|"
    r"(?:password|passwd|пароль|api[_ -]?key|токен)\s*[:=]\s*\S+)"
)
_MAX_ROWS = 512
_MAX_BYTES = 8 * 1024 * 1024


def clean_text(value, limit=4000):
    return (isinstance(value, str) and 0 < len(value) <= limit and value.strip()
            and not _SECRET.search(value) and "\x00" not in value)


def _questions(root, bid, review_hash):
    manifest = read_obj(root / (bid + ".manifest.json"))
    qids = manifest.get("question_ids")
    bh = manifest.get("batch_sha256")
    if (manifest.get("batch_id") != bid or manifest.get("repository") != "jaster351/AI-mentor"
            or not isinstance(bh, str) or not re.fullmatch(r"[0-9a-f]{64}", bh)
            or not isinstance(qids, list) or not 1 <= len(qids) <= 256
            or any(not isinstance(q, str) or not _QID.fullmatch(q) for q in qids)
            or len(set(qids)) != len(qids)):
        raise ValueError("Invalid mentor manifest")
    path = root / ("review-" + review_hash + ".json")
    if not path.is_file():
        path = root / "review.json"
    raw = read_bytes(path, 256 * 1024)
    if digest(raw) != review_hash:
        raise ValueError("Review bytes no longer match committed digest")
    review = json.loads(raw)
    if (not isinstance(review, dict) or review.get("schema") != "ai-mentor.evaluation.v1"
            or review.get("source") != "AI-mentor" or review.get("batch_id") != bid
            or review.get("batch_sha256") != bh):
        raise ValueError("Review does not match manifest")
    scores = review.get("scores")
    if not isinstance(scores, list) or len(scores) != len(qids):
        raise ValueError("Only fully reviewed batches are eligible")
    if any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in scores):
        raise ValueError("Invalid score row")
    by_id = {r["id"]: r for r in scores}
    if len(by_id) != len(scores) or set(by_id) != set(qids):
        raise ValueError("Invalid review coverage")
    history, examples, seen_questions = [], [], set()
    for qid in qids:
        rid = bid + "-" + qid
        q = read_obj(root / (rid + ".question.json"))
        a = read_obj(root / (rid + ".json"))
        marker = read_obj(root / (rid + ".published.json"))
        row = by_id[qid]
        question, answer = q.get("question"), a.get("answer")
        if (q.get("request_id") != rid or q.get("batch_sha256") != bh
                or a.get("request_id") != rid or a.get("batch_sha256") != bh
                or a.get("status") != "answered" or a.get("source") != "leshy_model"
                or not clean_text(question) or not clean_text(answer)
                or a.get("question_sha256") != digest(question.encode("utf-8"))
                or marker.get("request_id") != rid
                or marker.get("answer_sha256") != digest(answer.encode("utf-8"))
                or row.get("answer_sha256") != marker.get("answer_sha256")):
            raise ValueError("Unsafe or inconsistent synthetic exchange")
        # Do not train on the same current question across train/holdout.
        qkey = digest(" ".join(question.casefold().split()).encode("utf-8"))
        seen_questions.add(qkey)
        score = row.get("scores")
        if (not isinstance(score, dict) or set(score) != {"factuality", "constraints", "meaning"}
                or any(type(v) is not int or not 0 <= v <= 2 for v in score.values())
                or type(row.get("critical")) is not bool):
            raise ValueError("Invalid rubric")
        target = None
        if row.get("reaction") == "like" and not row["critical"] and all(v == 2 for v in score.values()):
            target = answer
        elif row.get("reaction") == "dislike" and clean_text(row.get("corrected_answer")):
            target = row["corrected_answer"]
        if target is not None:
            messages = [{"role": "system", "content": SYSTEM}]
            for prev_q, prev_a in history[-8:]:
                messages.extend([{"role": "user", "content": prev_q},
                                 {"role": "assistant", "content": prev_a}])
            messages.extend([{"role": "user", "content": question},
                             {"role": "assistant", "content": target}])
            examples.append({"id": rid, "question_sha256": qkey, "review_sha256": review_hash,
                             "reaction": row["reaction"], "messages": messages})
        # Preserve what the model actually saw, but never optimize on prior
        # answers; the trainer masks every token before the final target.
        history.append((question, answer))
    return examples, seen_questions


def build_dataset(root, train_batches, holdout_batches):
    if (not train_batches or not holdout_batches
            or len(set(train_batches + holdout_batches)) != len(train_batches + holdout_batches)
            or len(train_batches) + len(holdout_batches) > 32
            or any(not _BATCH.fullmatch(b) for b in train_batches + holdout_batches)):
        raise ValueError("Specify distinct reviewed training and holdout batches")
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid mentor root")
    committed = committed_reviews(read_obj(root / "load-policy.json"))
    splits = {}
    fingerprints = {}
    for name, batches in (("train", train_batches), ("holdout", holdout_batches)):
        rows, all_questions, by_question = [], set(), {}
        for bid in batches:
            if bid not in committed:
                raise ValueError("Review not committed: " + bid)
            examples, question_keys = _questions(root, bid, committed[bid])
            all_questions.update(question_keys)
            for row in examples:
                qkey = row["question_sha256"]
                # Deduplicate only identical prompt/target; differing context
                # may make the same short question legitimate in one split.
                key = digest(encode(row["messages"]))
                if key not in by_question:
                    rows.append(row)
                    by_question[key] = qkey
        if not rows or len(rows) > _MAX_ROWS:
            raise ValueError("Empty or oversized training split")
        splits[name] = rows
        fingerprints[name] = all_questions
    if fingerprints["train"] & fingerprints["holdout"]:
        raise ValueError("Exact normalized question overlap; select independent holdout")
    result = {"schema": "leshy.mentor-sft.v1", "train_batches": train_batches,
              "holdout_batches": holdout_batches, **splits}
    if len(encode(result)) > _MAX_BYTES:
        raise ValueError("Training dataset exceeds size bound")
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mentor-root", type=Path, required=True)
    p.add_argument("--train-batch", action="append", required=True)
    p.add_argument("--holdout-batch", action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    result = build_dataset(args.mentor_root, args.train_batch, args.holdout_batch)
    raw = encode(result)
    # New file only; do not overwrite any prior training artifact.
    with args.output.open("xb") as handle:
        handle.write(raw)
    print(json.dumps({"sha256": hashlib.sha256(raw).hexdigest(),
                      "train": len(result["train"]), "holdout": len(result["holdout"]) }))


if __name__ == "__main__":
    main()
