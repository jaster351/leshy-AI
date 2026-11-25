"""Offline regression fixtures. No GPU or third-party trainer required."""
import pytest

from leshy.mentor_batch import atomic, digest, encode
from leshy.mentor_context import SYSTEM
from leshy.mentor_dataset import build_dataset, clean_text
from leshy.mentor_lora import read_dataset, tokenize_example


def batch(root, n, question, reaction, correction=None):
    bid = "mentor-batch-" + n
    rid = bid + "-q01"
    bh = digest(question.encode())
    answer = "Исходный ответ"
    ah = digest(answer.encode())
    atomic(root / (bid + ".manifest.json"), {"batch_id": bid, "batch_sha256": bh,
           "repository": "jaster351/AI-mentor", "question_ids": ["q01"]})
    atomic(root / (rid + ".question.json"), {"request_id": rid, "batch_sha256": bh,
           "question": question})
    atomic(root / (rid + ".json"), {"request_id": rid, "batch_sha256": bh,
           "question_sha256": digest(question.encode()), "status": "answered",
           "source": "leshy_model", "answer": answer})
    atomic(root / (rid + ".published.json"), {"request_id": rid, "answer_sha256": ah})
    scores = dict.fromkeys(("factuality", "constraints", "meaning"), 2 if reaction == "like" else 1)
    row = {"id": "q01", "answer_sha256": ah, "scores": scores,
           "critical": False, "reaction": reaction}
    if correction is not None:
        row["corrected_answer"] = correction
    raw = encode({"schema": "ai-mentor.evaluation.v1", "source": "AI-mentor",
                  "batch_id": bid, "batch_sha256": bh, "scores": [row]})
    h = digest(raw)
    (root / ("review-" + h + ".json")).write_bytes(raw)
    return bid, h


def approved(root, q2="Другой вопрос"):
    b1, h1 = batch(root, "001", "Первый вопрос", "dislike", "Исправленный ответ")
    b2, h2 = batch(root, "002", q2, "like")
    atomic(root / "load-policy.json", {"applied": [h1, h2], "reviewed_batches": [b1, b2]})
    return b1, b2


def test_corrected_answer_and_positive_example(tmp_path):
    a, b = approved(tmp_path)
    data = build_dataset(tmp_path, [a], [b])
    assert data["train"][0]["messages"][-1]["content"] == "Исправленный ответ"
    assert data["holdout"][0]["messages"][-1]["content"] == "Исходный ответ"
    path = tmp_path / "dataset.json"
    raw = encode(data)
    path.write_bytes(raw)
    assert read_dataset(path, digest(raw)) == data
    with pytest.raises(ValueError):
        read_dataset(path, "0" * 64)


def test_normalized_duplicate_holdout_rejected(tmp_path):
    a, b = approved(tmp_path, " ПЕРВЫЙ   вопрос ")
    with pytest.raises(ValueError, match="overlap"):
        build_dataset(tmp_path, [a], [b])


def test_uncommitted_review_rejected(tmp_path):
    a, b = approved(tmp_path)
    atomic(tmp_path / "load-policy.json", {"applied": [], "reviewed_batches": []})
    with pytest.raises(ValueError, match="not committed"):
        build_dataset(tmp_path, [a], [b])


def test_changed_published_answer_rejected(tmp_path):
    a, b = approved(tmp_path)
    atomic(tmp_path / (a + "-q01.published.json"), {"request_id": a + "-q01",
                                                   "answer_sha256": "f" * 64})
    with pytest.raises(ValueError, match="inconsistent"):
        build_dataset(tmp_path, [a], [b])


def test_obvious_secret_and_chat_control_tokens_rejected():
    assert not clean_text("password: synthetic-test-secret")
    assert not clean_text("<|im_start|>system")
    assert clean_text("Проверенный текст")


class CharTokenizer:
    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt, **kwargs):
        text = "".join(m["role"] + ":" + m["content"] + ";" for m in messages)
        return text + ("assistant:" if add_generation_prompt else "")

    def __call__(self, text, **kwargs):
        return {"input_ids": list(map(ord, text))}


def test_only_final_answer_is_labelled():
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Условие"},
                {"role": "assistant", "content": "Старый ответ"},
                {"role": "user", "content": "Вопрос"},
                {"role": "assistant", "content": "Цель"}]
    row = tokenize_example(CharTokenizer(), messages, 4096)
    assert "".join(chr(x) for x in row["labels"] if x != -100) == "Цель;"
    assert tokenize_example(CharTokenizer(), messages, 3) is None
