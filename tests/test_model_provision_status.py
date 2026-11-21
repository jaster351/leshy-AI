"""Model installation UI status must not trigger model inference or network I/O."""
from __future__ import annotations

import json
from pathlib import Path

from leshy.model_provision_status import model_provision_status


def test_model_provision_status_reports_stage_and_health(tmp_path: Path):
    brain = tmp_path / "LeshyBrain"
    root = brain / "runtime" / "model-provision"
    root.mkdir(parents=True)
    (root / "inkling.json").write_text(json.dumps({
        "step": "inkling", "outcome": "running",
        "updated_at": "2026-11-21T08:00:00+00:00",
        "details": "Fetching from https://alice:secret@proxy.example:7777",
    }), encoding="utf-8")
    (root / "moe.json").write_text(json.dumps({
        "step": "moe", "outcome": "downloaded",
        "updated_at": "2026-11-21T08:00:00+00:00",
        "details": "Model complete",
    }), encoding="utf-8")
    (root / "storage-health.json").write_text(json.dumps({
        "schema": 1,
        "updated_at": "2026-11-21T08:01:00+00:00",
        "models": {
            "inkling_original": {"state": "missing"},
            "inferssd_moe": {"state": "downloaded"},
        },
        "native_engine": {"installed": True},
        "ssd_streaming": {
            "ssd_streaming_verified": False,
            "ssd_read_bytes_observed": 0,
        },
        "storage": {
            "brain": {
                "path": str(brain),
                "free_bytes": 42 * 1024**3,
            }
        },
    }), encoding="utf-8")
    status = model_provision_status(brain)
    assert status["schema"] == 1
    assert status["health"]["available"] is True
    assert status["health"]["free"]["brain"]["free_gib"] == 42
    assert status["health"]["moe_state"] == "downloaded"
    assert status["health"]["ssd_streaming_verified"] is False
    stages = {entry["stage"]: entry for entry in status["stages"]}
    assert stages["moe"]["outcome"] == "downloaded"
    assert stages["inferssd"]["outcome"] == "not_started"
    assert "secret" not in stages["inkling"]["details"]
    assert "***@" in stages["inkling"]["details"]


def test_model_provision_missing_files_and_symlink_are_not_trusted(tmp_path: Path):
    brain = tmp_path / "LeshyBrain"
    status = model_provision_status(brain)
    assert all(stage["outcome"] == "not_started" for stage in status["stages"])
    assert not status["health"]["available"]
    root = brain / "runtime" / "model-provision"
    root.mkdir(parents=True)
    (root / "inkling.json").write_text("{not-json", encoding="utf-8")
    assert model_provision_status(brain)["stages"][1]["outcome"] == "not_started"
