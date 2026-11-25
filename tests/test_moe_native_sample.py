"""Проверки ограниченной выборки без запуска модели."""
import json

import pytest

from leshy import moe_native_sample as sample


def fixture(tmp_path, events):
    model = tmp_path / "demo.gguf"
    model.write_bytes(b"GGUF")
    run = tmp_path / "run"
    run.mkdir()
    (run / "runtime.json").write_text(json.dumps({"argv": ["inferssd", "-m", str(model)]}),
                                      encoding="utf-8")
    path = run / "native-events.jsonl"
    path.write_bytes(b"".join((json.dumps(dict(event, v=1, seq=i)) + "\n").encode()
                              for i, event in enumerate(events)))
    return run, model, path


def begin():
    return {"ev": "ssd.controller.begin", "schema": 1}


def route():
    return {"ev": "ssd.place.layer", "success": True, "decode": True, "has_token": True,
            "selected_experts": [1, 2, 3, 4], "vram_hit": [True, False, True, False],
            "cache_hit": [False, True, True, False]}


def test_reports_observed_flags_not_physical_io(tmp_path):
    run, model, _ = fixture(tmp_path, [begin(), {"ev": "ssd.prefill.layer", "read_bytes": 4096},
                                       route(), route()])
    report = sample.sample_native_run(run, model)
    assert report["selected_expert_references"] == 8
    assert report["vram_hit_flags"] == report["cache_hit_flags"] == 4
    assert report["both_hit_flags"] == report["neither_hit_flags"] == 2
    assert report["vram_hit_fraction"] == 0.5
    assert report["declared_prefill_read_bytes"] == 4096
    assert report["physical_ssd_read_bytes"] is None
    assert report["model_weights_sha256_confirmed"] is False


def test_missing_samples_and_incomplete_line(tmp_path):
    run, model, path = fixture(tmp_path, [begin()])
    with path.open("ab") as stream:
        stream.write(b'{"v":1')
    report = sample.sample_native_run(run, model)
    assert report["termination"] == "incomplete_line"
    assert report["vram_hit_fraction"] is None
    assert report["declared_prefill_read_bytes"] is None


def test_reject_wrong_model_or_sequence(tmp_path):
    run, model, path = fixture(tmp_path, [begin(), route()])
    other = tmp_path / "other.gguf"
    other.write_bytes(b"GGUF")
    with pytest.raises(ValueError):
        sample.sample_native_run(run, other)
    raw = path.read_bytes().replace(b'"seq": 1', b'"seq": 9')
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        sample.sample_native_run(run, model)


def test_bounded_event_prefix(tmp_path, monkeypatch):
    run, model, _ = fixture(tmp_path, [begin(), route(), route()])
    monkeypatch.setattr(sample, "MAX_SAMPLE_EVENTS", 2)
    report = sample.sample_native_run(run, model)
    assert report["sample_events"] == 2
    assert report["termination"] == "event_budget"
    assert report["selected_expert_references"] == 4


def test_invalid_route_not_a_cache_miss(tmp_path):
    run, model, _ = fixture(tmp_path, [begin(), dict(route(), cache_hit=[False])])
    report = sample.sample_native_run(run, model)
    assert report["ignored_route_events"] == 1
    assert report["selected_expert_references"] == 0
    assert report["cache_hit_fraction"] is None
