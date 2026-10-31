from pathlib import Path
import shutil

import pytest

from leshy.accelerator_runtime import LlamaCppRuntimeManager
from leshy.diagnostics import close_diagnostics, configure_diagnostics, event, write_snapshot
from leshy.logging_paths import analysis_log_dir, central_log_dir, central_log_path


def test_all_native_backend_logs_share_one_root(tmp_path: Path) -> None:
    brain = tmp_path / "LeshyBrain"
    manager = LlamaCppRuntimeManager(brain)

    expected = brain / "logs"
    assert central_log_dir(brain) == expected
    assert manager.server_log_path("vulkan") == expected / "vulkan-server.log"
    assert manager.server_log_path("openvino") == expected / "openvino-server.log"
    assert manager.server_log_path("cpu") == expected / "cpu-server.log"

    runtime_root = brain / "runtime" / "llama.cpp"
    assert manager.server_log_path("openvino").parent != runtime_root


def test_log_path_cannot_escape_central_directory(tmp_path: Path) -> None:
    brain = tmp_path / "LeshyBrain"

    with pytest.raises(ValueError):
        central_log_path(brain, "runtime/openvino.log")
    with pytest.raises(ValueError):
        central_log_path(brain, "../openvino.log")


def test_analysis_log_dir_can_be_explicitly_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "analysis-log"
    monkeypatch.setenv("LESHY_ANALYSIS_LOG_DIR", str(destination))

    assert analysis_log_dir() == destination.resolve()
    assert destination.is_dir()


def test_diagnostics_are_automatically_mirrored_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = tmp_path / "LeshyBrain"
    destination = tmp_path / "repo" / "log"
    monkeypatch.setenv("LESHY_ANALYSIS_LOG_DIR", str(destination))
    close_diagnostics()
    try:
        configure_diagnostics(brain, "test-version")
        event("analysis_mirror_test", token="do-not-copy-this-secret")
        write_snapshot(brain, status="ok", api_key="do-not-copy-this-secret")
    finally:
        close_diagnostics()

    mirrored = (destination / "leshy-auto.log").read_text(encoding="utf-8")
    assert "analysis_mirror_test" in mirrored
    assert "do-not-copy-this-secret" not in mirrored
    assert "<redacted>" in mirrored

    snapshot = (destination / "diagnostics-auto.json").read_text(encoding="utf-8")
    assert "do-not-copy-this-secret" not in snapshot
    assert "<redacted>" in snapshot


def test_diagnostics_do_not_keep_brain_logs_locked(tmp_path: Path) -> None:
    brain = tmp_path / "removable" / "LeshyBrain"
    close_diagnostics()
    try:
        configure_diagnostics(brain, "test-version")
        event("windows_file_lock_regression")
        assert (brain / "logs" / "leshy.log").is_file()
        shutil.rmtree(brain)
        assert not brain.exists()
    finally:
        close_diagnostics()
