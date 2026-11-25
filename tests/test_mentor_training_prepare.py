"""Regression fixtures for the local preparation boundary; no GPU or network."""
import json

import pytest

from leshy.mentor_batch import atomic, digest
from leshy.mentor_training_prepare import prepare, validate_plan, _save_dataset


def plan():
    return {"schema": "leshy.mentor-training-plan.v1",
            "train_batches": ["mentor-batch-train"],
            "holdout_batches": ["mentor-batch-pilot"],
            "protected_batches": ["mentor-batch-audit"]}


def test_protected_batch_cannot_be_used_for_training():
    p = plan()
    p["train_batches"].append(p["protected_batches"][0])
    with pytest.raises(ValueError, match="disjoint"):
        validate_plan(p)


def test_missing_reviews_does_not_build_dataset(tmp_path):
    atomic(tmp_path / "load-policy.json", {"applied": [], "reviewed_batches": []})
    atomic(tmp_path / "training-plan.json", plan())
    assert prepare(tmp_path)["status"] == "awaiting_reviews"
    assert not (tmp_path / "training-datasets").exists()


def test_without_explicit_plan_does_not_build_dataset(tmp_path, monkeypatch):
    atomic(tmp_path / "load-policy.json", {"applied": ["a" * 64],
                                           "reviewed_batches": ["mentor-batch-train"]})
    monkeypatch.setattr("leshy.mentor_training_prepare._questions",
                        lambda *args: ([{}] * 21, set()))
    result = prepare(tmp_path)
    assert result["status"] == "awaiting_plan"
    assert result["eligible_examples"] == 21
    assert not (tmp_path / "training-datasets").exists()


def test_selected_splits_prepare_once_and_protect_audit(tmp_path, monkeypatch):
    p = plan()
    atomic(tmp_path / "training-plan.json", p)
    atomic(tmp_path / "load-policy.json", {
        "applied": ["a" * 64, "b" * 64, "c" * 64],
        "reviewed_batches": p["train_batches"] + p["holdout_batches"] + p["protected_batches"]})
    seen = []
    def inspect(root, bid, sha):
        seen.append(bid)
        return [{}], set()
    def build(root, train, holdout):
        assert train == p["train_batches"] and holdout == p["holdout_batches"]
        return {"schema": "leshy.mentor-sft.v1", "train": [{}] * 16, "holdout": [{}] * 8}
    monkeypatch.setattr("leshy.mentor_training_prepare._questions", inspect)
    monkeypatch.setattr("leshy.mentor_training_prepare.build_dataset", build)
    first = prepare(tmp_path)
    second = prepare(tmp_path)
    assert first == second
    assert first["status"] == "prepared"
    assert p["protected_batches"][0] not in seen
    path = tmp_path / "training-datasets" / (first["dataset_sha256"] + ".json")
    assert digest(path.read_bytes()) == first["dataset_sha256"]
    assert json.loads(path.read_bytes())["protected_batches"] == p["protected_batches"]
    assert len(list(path.parent.iterdir())) == 1


def test_revoked_permission_does_not_write(tmp_path):
    assert prepare(tmp_path, allowed=lambda: False) == {"status": "disabled"}
    assert not (tmp_path / "training-preparation.json").exists()


def test_conflicting_existing_dataset_is_not_overwritten(tmp_path):
    raw = b'{"synthetic":true}'
    sha = _save_dataset(tmp_path, raw)
    path = tmp_path / "training-datasets" / (sha + ".json")
    path.write_bytes(b"other")
    with pytest.raises(ValueError, match="mismatch"):
        _save_dataset(tmp_path, raw)
    assert path.read_bytes() == b"other"
