from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error

from leshy.accelerator_runtime import LlamaServerLLM
from leshy.adaptive_backend import AdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable


class _Manager:
    def ensure(self, backend):
        return Path("llama-server")

    def server_log_path(self, backend):
        return Path("server.log")


class _Provider:
    def __init__(self, name, *, answer="ok", fail=None):
        self.name = name
        self.answer = answer
        self.fail = fail
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.answer

    def warmup(self):
        if self.fail:
            raise self.fail
        return True


class AcceleratorRuntimeRegressionTests(unittest.TestCase):
    def _provider(self, backend="vulkan"):
        return LlamaServerLLM(
            manager=_Manager(),
            backend=backend,
            model_path=Path("model.gguf"),
            ensure_model=lambda: Path("model.gguf"),
            model="model.gguf",
            n_ctx=2048,
            startup_timeout=1,
            request_timeout=1,
            gpu_count=2,
        )

    def test_adaptive_warmup_falls_back_when_gpu_fails(self):
        class WarmFail(_Provider):
            def warmup(self):
                raise RuntimeError("warmup failed")
        gpu = WarmFail("gpu-vulkan")
        cpu = _Provider("cpu-native")
        router = AdaptiveLLM([gpu, cpu], cooldown_seconds=60)
        self.assertTrue(router.warmup())
        self.assertEqual(router.status()["active_backend"], "cpu-native")

    def test_adaptive_warmup_uses_gpu_first_and_exposes_resident_state(self):
        gpu = _Provider("gpu-vulkan")
        cpu = _Provider("cpu-native")
        router = AdaptiveLLM([gpu, cpu])
        self.assertTrue(router.warmup())
        self.assertEqual(router.status()["active_backend"], "gpu-vulkan")

    def test_http_503_stops_backend_and_raises_llm_unavailable(self):
        provider = self._provider("openvino")
        provider._ensure_server = lambda: "http://127.0.0.1:12345"
        stopped = []
        provider._stop_process = lambda: stopped.append(True)
        exc = error.HTTPError("http://127.0.0.1:12345/v1/chat/completions", 503, "busy", hdrs=None, fp=None)
        with patch("leshy.accelerator_runtime.request.urlopen", side_effect=exc):
            with self.assertRaises(LLMUnavailable):
                provider.chat([ChatMessage("user", "hello")])
        self.assertEqual(stopped, [True])

    def test_openvino_503_fails_over_to_cpu_without_retrying_failed_backend(self):
        npu = self._provider("openvino")
        npu._ensure_server = lambda: "http://127.0.0.1:12345"
        npu._stop_process = lambda: None
        cpu = _Provider("cpu-native", answer="fallback-ok")
        router = AdaptiveLLM([npu, cpu], cooldown_seconds=60)
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
        npu_status = next(item for item in status["backends"] if item["name"] == "openvino" or item["name"] == "npu-openvino")
        self.assertGreater(npu_status["cooldown_remaining_seconds"], 0)

    def test_vulkan_uses_layer_split_and_fit_without_conflicting_ngl_all(self):
        # Vulkan остаётся vendor-neutral, а --fit должен сам распределять слои по доступной VRAM.
        import inspect

        source = inspect.getsource(LlamaServerLLM._ensure_server)
        self.assertNotIn('"-ngl", "all"', source)
        self.assertIn('"-sm", "layer"', source)
        self.assertIn('"--fit", "on"', source)
        self.assertNotIn('"--no-warmup"', source)

    def test_llama_server_warmup_only_ensures_resident_server(self):
        provider = self._provider("vulkan")
        calls = []
        provider._ensure_server = lambda: calls.append(True) or "http://127.0.0.1:12345"
        self.assertTrue(provider.warmup())
        self.assertEqual(calls, [True])


if __name__ == "__main__":
    unittest.main()
