from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from leshy.app import LeshyApp
from leshy.local_platform import (
    installed_llama_server,
    local_backend_order,
    runtime_platform,
)


class PlatformDetectorTests(unittest.TestCase):
    def test_windows_is_not_regressed(self):
        self.assertEqual(runtime_platform("Windows"), "windows")
        self.assertEqual(
            local_backend_order(system="Windows", has_native_gpu=True, has_server=True),
            ("cpu-llama-server", "cpu-native"),
        )
        self.assertIsNone(installed_llama_server(system="Windows", environ={}))

    def test_linux_and_other_unix_are_detected(self):
        self.assertEqual(runtime_platform("Linux"), "linux")
        self.assertEqual(runtime_platform("Darwin"), "other")
        self.assertEqual(
            local_backend_order(system="Linux", has_native_gpu=True, has_server=True),
            ("gpu-native", "cpu-native", "cpu-llama-server"),
        )

    def test_linux_local_binary_detected_by_explicit_path(self):
        with TemporaryDirectory() as root:
            binary = Path(root) / "llama-server"
            binary.write_bytes(b"binary placeholder, not executed")
            binary.chmod(0o755)
            self.assertEqual(
                installed_llama_server(
                    system="Linux",
                    environ={"LESHY_LLAMA_SERVER": str(binary)},
                ),
                binary.resolve(),
            )

    def test_invalid_linux_binary_is_not_advertised(self):
        with patch("leshy.local_platform.shutil.which", return_value=None):
            self.assertIsNone(installed_llama_server(
                system="Linux",
                environ={"LESHY_LLAMA_SERVER": "/missing/llama-server"},
            ))


class AppRuntimeRoutingTests(unittest.TestCase):
    @staticmethod
    def _app(caps):
        app = LeshyApp.__new__(LeshyApp)
        app.settings = SimpleNamespace(
            llm_backend="native", native_context_size=4096,
        )
        app.model_path = Path("/nonexistent/test.gguf")
        app.accelerator_runtime = object()
        app.hardware = caps
        return app

    def test_windows_keeps_gpu_npu_server_and_native_order(self):
        caps = SimpleNamespace(
            gpu=True, gpu_names=("AMD Radeon",),
            npu=True, npu_names=("Intel NPU",),
            gpu_offload_runtime=True,
        )
        with patch("leshy.app.runtime_platform", return_value="windows"):
            llm = self._app(caps)._build_llm()
        self.assertEqual(
            [state.name for state in llm._states],
            ["gpu-vulkan", "npu-openvino", "cpu-llama-server", "cpu-native"],
        )

    def test_linux_cpu_works_without_windows_package(self):
        caps = SimpleNamespace(
            gpu=False, gpu_names=(), npu=False, npu_names=(),
            gpu_offload_runtime=False,
        )
        with patch("leshy.app.runtime_platform", return_value="linux"), \
             patch("leshy.app.installed_llama_server", return_value=None):
            llm = self._app(caps)._build_llm()
        self.assertEqual([state.name for state in llm._states], ["cpu-native"])

    def test_linux_gpu_and_system_server_are_used_only_if_present(self):
        caps = SimpleNamespace(
            gpu=True, gpu_names=("GPU",), npu=False, npu_names=(),
            gpu_offload_runtime=True,
        )
        with patch("leshy.app.runtime_platform", return_value="linux"), \
             patch("leshy.app.installed_llama_server", return_value=Path("/usr/bin/llama-server")):
            llm = self._app(caps)._build_llm()
        self.assertEqual(
            [state.name for state in llm._states],
            ["gpu-native", "cpu-native", "cpu-llama-server"],
        )


if __name__ == "__main__":
    unittest.main()
