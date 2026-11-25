"""Regression fixtures: feedback changes investigation, not throughput."""
import pytest

from leshy.mentor_batch import atomic, digest, encode, now, read_bytes, read_obj
from leshy.mentor_review import apply_review, MAX_PART


def apply(root, approval=""):
    return apply_review(root, {"repository": "jaster351/AI-mentor",
                               "approved_review_sha256": approval},
                        read_obj=read_obj, read_bytes=read_bytes, atomic=atomic,
                        digest=digest, now=now)


def test_legacy_limit_migrates_without_reapplying_feedback(tmp_path):
    old = {"quota": 3, "maximum_confirmed": 30, "applied": ["a" * 64],
           "reviewed_batches": ["mentor-batch-004"],
           "last_review": {"critical": True}, "investigation": [{"id": "q17"}],
           "quality_history": []}
    atomic(tmp_path / "load-policy.json", old)
    state = apply(tmp_path)
    assert state["quota"] == MAX_PART
    assert state["previous_quota"] == 3
    for key in ("applied", "reviewed_batches", "last_review", "investigation",
                "quality_history", "maximum_confirmed"):
        assert state[key] == old[key]
    assert read_obj(tmp_path / "load-policy.json") == state
    assert apply(tmp_path) == state


def test_duplicate_review_migrates_without_reading_or_reapplying(tmp_path):
    h = "a" * 64
    atomic(tmp_path / "load-policy.json", {"quota": 3, "applied": [h],
                                           "reviewed_batches": ["mentor-batch-004"]})
    assert apply(tmp_path, h)["quota"] == MAX_PART
    assert apply(tmp_path, h)["applied"] == [h]


@pytest.mark.parametrize("critical,score", [(True, 0), (False, 0), (False, 2)])
def test_quality_does_not_throttle_and_investigation_is_preserved(tmp_path, critical, score):
    bid, bh = "mentor-batch-005", "b" * 64
    rid = bid + "-q01"
    answer = "Synthetic response"
    ah = digest(answer.encode("utf-8"))
    atomic(tmp_path / "load-policy.json", {"quota": 3, "maximum_confirmed": 0,
                                           "applied": [], "reviewed_batches": [],
                                           "investigation": [], "quality_history": []})
    atomic(tmp_path / (bid + ".manifest.json"), {"batch_id": bid, "batch_sha256": bh,
            "repository": "jaster351/AI-mentor", "question_ids": ["q01"]})
    atomic(tmp_path / (rid + ".json"), {"request_id": rid, "batch_sha256": bh,
            "status": "answered", "source": "leshy_model", "answer": answer,
            "model": "not_confirmed", "revision": "1.10.234"})
    atomic(tmp_path / (rid + ".published.json"), {"request_id": rid, "answer_sha256": ah})
    report = {"schema": "ai-mentor.evaluation.v1", "source": "AI-mentor",
              "batch_id": bid, "batch_sha256": bh, "benchmark_id": "demo",
              "rubric_id": "demo", "comparable": False, "scores": [
                  {"id": "q01", "answer_sha256": ah,
                   "scores": {"factuality": score, "constraints": score, "meaning": score},
                   "critical": critical, "cause": "unknown" if score == 0 else "none",
                   "reaction": "dislike" if score == 0 else "like"}]}
    raw = encode(report)
    (tmp_path / "review.json").write_bytes(raw)
    state = apply(tmp_path, digest(raw))
    assert state["quota"] == MAX_PART
    assert state["last_review"]["critical"] is critical
    assert bool(state["investigation"]) == (score == 0)
    assert state["maximum_confirmed"] == 0


def test_corrupt_legacy_limit_is_not_silently_repaired(tmp_path):
    atomic(tmp_path / "load-policy.json", {"quota": -1})
    with pytest.raises(ValueError):
        apply(tmp_path)
