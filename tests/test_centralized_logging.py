from pathlib import Path

import pytest

from leshy.accelerator_runtime import LlamaCppRuntimeManager
from leshy.logging_paths import central_log_dir, central_log_path


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
