from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib import error

from leshy.accelerator_runtime import LlamaServerLLM
from leshy.adaptive_backend import AdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable


class _Manager:
    def server_log_path(self, backend):
        raise AssertionError("server_log_path не должен использоваться в этом тесте")


class _Fallback:
    model = "fallback"

    def __init__(self):
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return "fallback-ok"


class AcceleratorRuntimeRegressionTests(unittest.TestCase):
    def _provider(self, backend="openvino"):
        provider = LlamaServerLLM(
            _Manager(),
            backend,
            __file__,
            ensure_model=lambda: __file__,
            startup_timeout=5,
            request_timeout=10,
        )
        provider._ensure_server = lambda: "http://127.0.0.1:12345"
        return provider

    def test_http_503_stops_backend_and_raises_llm_unavailable(self):
        provider = self._provider("openvino")
        stopped = []
        provider._stop_process = lambda: stopped.append(True)
        exc = error.HTTPError(
            "http://127.0.0.1:12345/v1/chat/completions",
            503,
            "busy",
            hdrs=None,
            fp=None,
        )
        with patch("leshy.accelerator_runtime.request.urlopen", side_effect=exc):
            with self.assertRaisesRegex(LLMUnavailable, "HTTP 503"):
                provider.chat([ChatMessage("user", "test")])
        self.assertEqual(stopped, [True])

    def test_openvino_503_fails_over_to_cpu_without_retrying_failed_backend(self):
        openvino = self._provider("openvino")
        openvino._stop_process = lambda: None
        cpu = _Fallback()
        router = AdaptiveLLM(
            [("npu-openvino", openvino), ("cpu-native", cpu)],
            cooldown_seconds=60,
        )
        exc = error.HTTPError(
            "http://127.0.0.1:12345/v1/chat/completions",
            503,
            "busy",
            hdrs=None,
            fp=None,
        )
        with patch("leshy.accelerator_runtime.request.urlopen", side_effect=exc) as urlopen:
            self.assertEqual(router.chat([ChatMessage("user", "first")]), "fallback-ok")
            self.assertEqual(router.chat([ChatMessage("user", "second")]), "fallback-ok")
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(cpu.calls, 2)
        status = router.status()
        npu = next(item for item in status["backends"] if item["name"] == "npu-openvino")
        self.assertGreater(npu["cooldown_remaining_seconds"], 0)

    def test_vulkan_uses_all_gpu_layers_layer_split_and_fit(self):
        # Контракт аргументов запуска проверяется статически, чтобы regression
        # работал и на CI без физического GPU/Vulkan runtime.
        import inspect

        source = inspect.getsource(LlamaServerLLM._ensure_server)
        self.assertIn('"-ngl", "all"', source)
        self.assertIn('"-sm", "layer"', source)
        self.assertIn('"--fit", "on"', source)


if __name__ == "__main__":
    unittest.main()
