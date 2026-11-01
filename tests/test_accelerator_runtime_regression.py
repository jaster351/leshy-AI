from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from leshy.accelerator_runtime import LlamaCppRuntimeManager, LlamaServerLLM
from leshy.adaptive_backend import AdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable


class _Fallback:
    model = "fallback"
    backend = "cpu"

    def __init__(self):
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return "fallback-ok"

    def status(self):
        return {"backend": self.backend, "model": self.model}


class AcceleratorRuntimeRegressionTests(unittest.TestCase):
    def _provider(self, backend: str = "vulkan"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        model = root / "model.gguf"
        model.write_bytes(b"x")
        manager = LlamaCppRuntimeManager(root)
        return LlamaServerLLM(manager, backend, model, ensure_model=lambda: model)

    def test_adaptive_warmup_falls_back_when_gpu_fails(self):
        class _Broken:
            model = "broken"
            backend = "vulkan"
            def warmup(self):
                raise RuntimeError("warmup failed")
            def status(self):
                return {"backend": self.backend, "model": self.model}

        fallback = _Fallback()
        fallback.warmup = lambda: True
        router = AdaptiveLLM([("gpu-vulkan", _Broken()), ("cpu-native", fallback)])
        self.assertEqual(router.warmup(), "cpu-native")
        self.assertEqual(router.status()["preferred"], "cpu-native")

    def test_adaptive_warmup_uses_gpu_first_and_exposes_resident_state(self):
        class _Ready:
            model = "ready"
            backend = "vulkan"
            def warmup(self): return True
            def status(self): return {"backend": self.backend, "model": self.model, "resident": True}
        router = AdaptiveLLM([("gpu-vulkan", _Ready()), ("cpu-native", _Fallback())])
        self.assertEqual(router.warmup(), "gpu-vulkan")
        self.assertEqual(router.status()["preferred"], "gpu-vulkan")

    def test_http_503_stops_backend_and_raises_llm_unavailable(self):
        provider = self._provider("openvino")
        provider._ensure_server = lambda: "http://127.0.0.1:12345"
        provider._process = unittest.mock.Mock()
        provider._process.poll.return_value = None
        exc = HTTPError("http://127.0.0.1:12345/v1/chat/completions", 503, "busy", hdrs=None, fp=None)
        with patch("leshy.accelerator_runtime.request.urlopen", side_effect=exc):
            with self.assertRaises(LLMUnavailable):
                provider.chat([ChatMessage("user", "hello")])

    def test_openvino_503_fails_over_to_cpu_without_retrying_failed_backend(self):
        provider = self._provider("openvino")
        provider._ensure_server = lambda: "http://127.0.0.1:12345"
        provider._process = unittest.mock.Mock()
        provider._process.poll.return_value = None
        cpu = _Fallback()
        router = AdaptiveLLM([("npu-openvino", provider), ("cpu-native", cpu)], cooldown_seconds=60)
        exc = HTTPError("http://127.0.0.1:12345/v1/chat/completions", 503, "busy", hdrs=None, fp=None)
        with patch("leshy.accelerator_runtime.request.urlopen", side_effect=exc) as urlopen:
            self.assertEqual(router.chat([ChatMessage("user", "first")]), "fallback-ok")
            self.assertEqual(router.chat([ChatMessage("user", "second")]), "fallback-ok")
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(cpu.calls, 2)
        status = router.status()
        npu = next(item for item in status["backends"] if item["name"] == "npu-openvino")
        self.assertGreater(npu["cooldown_remaining_seconds"], 0)

    def test_vulkan_uses_layer_split_and_fit_without_forcing_all_layers(self):
        # --fit должен сам подобрать число GPU-слоёв по свободной VRAM.
        # Одновременный -ngl all отключает этот защитный механизм в свежем llama.cpp.
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
