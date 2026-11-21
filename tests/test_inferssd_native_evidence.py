"""Offline evidence contracts; never start a real model in these tests."""
from __future__ import annotations

import json
from pathlib import Path

from leshy.inferssd_observations import native_streaming_status


def _write_run(home: Path, model: Path, events: list[dict]) -> Path:
    directory = home / "state" / "runs" / "test-run-123"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "runtime.json").write_text(
        json.dumps({"argv": ["inferssd-server", "-m", str(model.resolve())]}),
        encoding="utf-8",
    )
    (directory / "native-events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8",
    )
    return directory


def _native_events(*, read_bytes: int = 4096, wrong_sequence: bool = False) -> list[dict]:
    route = {
        "v": 1, "seq": 2 if not wrong_sequence else 9,
        "ev": "ssd.place.layer", "success": True,
        "decode": True, "has_token": True,
        "selected_experts": [3, 9], "vram_hit": [False, True],
        "cache_hit": [False, False],
    }
    return [
        {"v": 1, "seq": 0, "ev": "ssd.controller.begin", "schema": 1},
        {"v": 1, "seq": 1, "ev": "ssd.prefill.layer", "read_bytes": read_bytes},
        route,
    ]


def test_native_inferssd_requires_positive_reads_and_routed_experts(tmp_path: Path):
    model = tmp_path / "qwen-moe.gguf"
    model.write_bytes(b"GGUFdemo")
    home = tmp_path / "inferssd"
    _write_run(home, model, _native_events())
    state = native_streaming_status(model, home)
    assert state["ssd_streaming_verified"] is True
    assert state["ssd_read_bytes_observed"] == 4096
    assert state["routed_layers_observed"] == 1

    _write_run(home, model, _native_events(read_bytes=0))
    assert native_streaming_status(model, home)["ssd_streaming_verified"] is False


def test_native_inferssd_rejects_wrong_model_or_sequence(tmp_path: Path):
    model = tmp_path / "qwen-moe.gguf"
    other = tmp_path / "another-model.gguf"
    model.write_bytes(b"GGUFdemo")
    other.write_bytes(b"GGUFdemo")
    home = tmp_path / "inferssd"
    _write_run(home, other, _native_events())
    assert native_streaming_status(model, home)["ssd_streaming_verified"] is False

    _write_run(home, model, _native_events(wrong_sequence=True))
    assert native_streaming_status(model, home)["ssd_streaming_verified"] is False


def test_native_inferssd_does_not_trust_started_or_empty_files(tmp_path: Path):
    model = tmp_path / "qwen-moe.gguf"
    model.write_bytes(b"GGUFdemo")
    home = tmp_path / "inferssd"
    directory = _write_run(
        home, model, [{"v": 1, "seq": 0, "ev": "ssd.controller.begin", "schema": 1}],
    )
    assert native_streaming_status(model, home)["ssd_streaming_verified"] is False
    (directory / "native-events.jsonl").write_text(
        '{"v":1,"seq":0,"ev":"ssd.controller.begin","schema":1}\n'
        '{"v":1,"seq":1,"ev":"ssd.prefill.layer","read_bytes":4096}',
        encoding="utf-8",
    )
    assert native_streaming_status(model, home)["ssd_streaming_verified"] is False
