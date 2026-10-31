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


class _WarmProvider:
    model = "warm"

    def __init__(self, fail=False):
        self.fail = fail
        self.warm_calls = 0
        self.chat_calls = 0
        self.resident = False

    def warmup(self):
        self.warm_calls += 1
        if self.fail:
            raise RuntimeError("warmup failed")
        self.resident = True
        return True

    def chat(self, messages):
        self.chat_calls += 1
        return "warm-ok"

    def resident_status(self):
        return {"resident": self.resident}


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
        # Vulkan is intentionally vendor-neutral on Windows: Radeon, NVIDIA and Intel.
        import inspect

        source = inspect.getsource(LlamaServerLLM._ensure_server)
        self.assertIn('"-ngl", "all"', source)
        self.assertIn('"-sm", "layer"', source)
        self.assertIn('"--fit", "on"', source)
        self.assertNotIn('"--no-warmup"', source)

    def test_llama_server_warmup_only_ensures_resident_server(self):
        provider = self._provider("vulkan")
        calls = []
        provider._ensure_server = lambda: calls.append(True) or "http://127.0.0.1:12345"
        self.assertTrue(provider.warmup())
        self.assertEqual(calls, [True])

    def test_adaptive_warmup_uses_gpu_first_and_exposes_resident_state(self):
        gpu = _WarmProvider()
        cpu = _WarmProvider()
        router = AdaptiveLLM([("gpu-vulkan", gpu), ("cpu-native", cpu)], cooldown_seconds=60)
        self.assertEqual(router.warmup(), "gpu-vulkan")
        self.assertEqual(gpu.warm_calls, 1)
        self.assertEqual(cpu.warm_calls, 0)
        status = router.status()
        gpu_status = next(item for item in status["backends"] if item["name"] == "gpu-vulkan")
        self.assertTrue(gpu_status["resident"]["resident"])

    def test_adaptive_warmup_falls_back_when_gpu_fails(self):
        gpu = _WarmProvider(fail=True)
        cpu = _WarmProvider()
        router = AdaptiveLLM([("gpu-vulkan", gpu), ("cpu-native", cpu)], cooldown_seconds=60)
        self.assertEqual(router.warmup(), "cpu-native")
        self.assertEqual(gpu.warm_calls, 1)
        self.assertEqual(cpu.warm_calls, 1)


if __name__ == "__main__":
    unittest.main()
