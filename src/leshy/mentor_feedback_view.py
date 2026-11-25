"""Read-only view of committed LiveMentor feedback, not an application path."""
from __future__ import annotations

import re
from hashlib import sha256

_BATCH = re.compile(r"mentor-batch-[a-z0-9-]{1,55}")
_ANSWER = re.compile(r"(mentor-batch-[a-z0-9-]{1,55})-(q[0-9]{1,6})")
_QID = re.compile(r"q[0-9]{1,6}")
_HASH = re.compile(r"[0-9a-f]{64}")
_CAUSES = {"none", "question", "rubric", "knowledge", "context", "code",
           "model", "resources", "transport", "unknown"}


def _match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def committed_reviews(load):
    """The two lists are appended together by the transactional review writer."""
    applied = load.get("applied")
    batches = load.get("reviewed_batches")
    if (not isinstance(applied, list) or not isinstance(batches, list)
            or len(applied) != len(batches) or len(applied) > 4096
            or any(not _match(_HASH, h) for h in applied)
            or any(not _match(_BATCH, bid) for bid in batches)
            or len(set(applied)) != len(applied) or len(set(batches)) != len(batches)):
        return {}
    return dict(zip(batches, applied))


def _number(value, low, high):
    return type(value) in (int, float) and low <= value <= high


def committed_summary(load, reviews):
    """Do not show staged or partially written review records as applied."""
    last = load.get("last_review")
    if not isinstance(last, dict):
        return {}
    batch, approval = last.get("batch_id"), last.get("review_sha256")
    if (not _match(_BATCH, batch) or not _match(_HASH, approval)
            or reviews.get(batch) != approval):
        return {}
    quality, count, total = last.get("quality"), last.get("reviewed"), last.get("total")
    if (not _number(quality, 0, 1) or type(count) is not int
            or type(total) is not int or not 1 <= count <= total <= 256
            or last.get("complete") is not (count == total)):
        return {}
    result = {"batch_id": batch, "review_sha256": approval, "quality": quality,
              "reviewed": count, "total": total, "complete": count == total,
              "comparable": last.get("comparable") is True,
              "critical": last.get("critical") is True}
    for key in ("timestamp", "model", "revision"):
        value = last.get(key)
        if isinstance(value, str):
            result[key] = value[:80]
    counts = last.get("reactions")
    if isinstance(counts, dict):
        values = {k: counts.get(k) for k in ("like", "dislike", "missing")}
        if (all(type(v) is int and 0 <= v <= count for v in values.values())
                and sum(values.values()) == count):
            result["reactions"] = values
    # New reports store the paired score independently of longitudinal
    # comparability. Older comparable reports already stored it in comparison.
    ref = last.get("reference") or last.get("comparison")
    if isinstance(ref, dict) and count == total:
        base, relative = ref.get("reference_percent"), ref.get("relative_percent")
        if _number(base, 0, 100) and base > 0 and _number(relative, 0, 153600):
            result.update(reference_percent=base, relative_percent=relative)
    return result


def attach_feedback(items, root, reviews, read):
    """Load at most one bounded journal per batch visible on the current page."""
    cached = {}
    for item in items:
        rid = item.get("request_id")
        match = _ANSWER.fullmatch(rid) if isinstance(rid, str) else None
        if (not match or item.get("status") != "published"
                or item.get("source") != "leshy_model"
                or not isinstance(item.get("answer"), str) or not item["answer"]):
            continue
        batch, qid = match.groups()
        approval = reviews.get(batch)
        if not approval:
            continue
        if batch not in cached:
            journal = read(root / (batch + ".reactions-" + approval + ".json"), 1024 * 1024)
            rows = journal.get("reactions")
            by_id = {}
            if (journal.get("source") == "AI-mentor" and journal.get("batch_id") == batch
                    and journal.get("review_sha256") == approval
                    and isinstance(rows, list) and len(rows) <= 256):
                for row in rows:
                    if (not isinstance(row, dict) or not _match(_QID, row.get("id"))
                            or row["id"] in by_id):
                        by_id = {}
                        break
                    by_id[row["id"]] = row
            cached[batch] = by_id
        row = cached[batch].get(qid)
        if not row:
            continue
        reaction, cause = row.get("reaction"), row.get("cause")
        if (reaction not in ("like", "dislike") or not isinstance(cause, str)
                or cause not in _CAUSES or (reaction == "dislike" and cause == "none")
                or row.get("answer_sha256") != sha256(item["answer"].encode("utf-8")).hexdigest()):
            continue
        feedback = {"reaction": reaction, "cause": cause, "review_sha256": approval}
        if reaction == "dislike":
            for key, limit in (("reason", 1000), ("corrected_answer", 4000)):
                value = row.get(key)
                if isinstance(value, str) and 0 < len(value) <= limit:
                    feedback[key] = value
        item["feedback"] = feedback
