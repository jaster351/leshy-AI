"""Offline regression fixtures for the owner-enabled automatic mentor feedback."""
import base64
import json
import threading
from urllib.error import HTTPError

from leshy import mentor_batch
from leshy.mentor_batch import BatchRunner, atomic, digest, encode, read_obj


def setup_exchange(tmp_path, monkeypatch):
    root = tmp_path
    token = "ghu_test_feedback_token"
    monkeypatch.setattr(mentor_batch, "load_secret", lambda _: token)
    config = {"enabled": True, "repository": "jaster351/AI-mentor",
              "batch_id": "mentor-batch-003", "batch_sha256": "c" * 64,
              "auto_queue": True, "auto_publish": True}
    atomic(root / "config.json", config)
    runner = BatchRunner(root, object(), threading.Event(), None)
    bid, bh = "mentor-batch-002", "b" * 64
    atomic(root / (bid + ".manifest.json"), {"batch_id": bid, "batch_sha256": bh,
           "repository": config["repository"], "question_ids": ["q01"]})
    rid = bid + "-q01"
    answer = "A"
    ah = digest(answer.encode())
    atomic(root / (rid + ".json"), {"request_id": rid, "batch_sha256": bh,
           "status": "answered", "source": "leshy_model", "answer": answer,
           "model": "not_confirmed", "revision": "1.10.229"})
    atomic(root / (rid + ".published.json"), {"request_id": rid, "answer_sha256": ah})
    report = {"schema": "ai-mentor.evaluation.v1", "source": "AI-mentor",
              "batch_id": bid, "batch_sha256": bh, "benchmark_id": "demo",
              "rubric_id": "demo", "comparable": False, "scores": [
                  {"id": "q01", "answer_sha256": ah, "scores": {
                      "factuality": 2, "constraints": 2, "meaning": 2},
                   "critical": False, "cause": "none", "reaction": "like"}]}
    raw = encode(report)
    h = digest(raw)
    index = {"schema": "ai-mentor.review-index.v1", "reviews": [
        {"batch_id": bid, "batch_sha256": bh, "review_sha256": h}]}
    remote = {"/contents/reviews/index.json?ref=main": encode(index),
              "/contents/reviews/" + h + ".json?ref=main": raw}
    calls = []
    def gh(path):
        calls.append(path)
        if path not in remote:
            raise HTTPError(path, 404, "missing", None, None)
        return {"type": "file", "encoding": "base64",
                "content": base64.b64encode(remote[path]).decode()}
    state = {"quota": 30, "applied": [], "reviewed_batches": []}
    return runner, config, token, gh, remote, calls, state, h


def test_import_previous_batch_and_deduplicate(tmp_path, monkeypatch):
    runner, cfg, token, gh, remote, calls, state, h = setup_exchange(tmp_path, monkeypatch)
    result = runner.receive_automatic_reviews(cfg, gh, token, state)
    assert result["applied"] == [h]
    assert result["last_review"]["reactions"]["like"] == 1
    assert read_obj(tmp_path / "load-policy.json") == result
    assert read_obj(tmp_path / "config.json") == cfg
    again = runner.receive_automatic_reviews(cfg, gh, token, result)
    assert again == result
    assert calls.count("/contents/reviews/" + h + ".json?ref=main") == 1


def test_disabled_automatic_review_does_not_fetch(tmp_path, monkeypatch):
    runner, cfg, token, gh, remote, calls, state, h = setup_exchange(tmp_path, monkeypatch)
    cfg["auto_review"] = False
    atomic(tmp_path / "config.json", cfg)
    assert runner.receive_automatic_reviews(cfg, gh, token, state) == state
    assert calls == []


def test_bad_digest_keeps_cycle_state(tmp_path, monkeypatch):
    runner, cfg, token, gh, remote, calls, state, h = setup_exchange(tmp_path, monkeypatch)
    remote["/contents/reviews/" + h + ".json?ref=main"] += b" "
    assert runner.receive_automatic_reviews(cfg, gh, token, state) == state
    assert not (tmp_path / "load-policy.json").exists()


def test_unpublished_answer_is_not_scored(tmp_path, monkeypatch):
    runner, cfg, token, gh, remote, calls, state, h = setup_exchange(tmp_path, monkeypatch)
    (tmp_path / "mentor-batch-002-q01.published.json").unlink()
    assert runner.receive_automatic_reviews(cfg, gh, token, state) == state
    assert not (tmp_path / "load-policy.json").exists()


def test_missing_index_is_backward_compatible(tmp_path, monkeypatch):
    runner, cfg, token, gh, remote, calls, state, h = setup_exchange(tmp_path, monkeypatch)
    remote.clear()
    assert runner.receive_automatic_reviews(cfg, gh, token, state) == state
