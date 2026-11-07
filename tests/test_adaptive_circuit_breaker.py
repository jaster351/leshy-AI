from __future__ import annotations

import unittest
import time

from leshy.adaptive_backend import AdaptiveEmbeddings, AdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable


class FakeLLM:
    def __init__(self, answer="ok", error=None):
        self.answer = answer
        self.error = error
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.error:
            raise LLMUnavailable(self.error)
        return self.answer


class FakeEmbeddings:
    model = "fake:test"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def available(self):
        return True

    def embed(self, text):
        self.calls += 1
        if self.fail:
            raise RuntimeError("temporary failure")
        return [0.1]


class AdaptiveCircuitBreakerTests(unittest.TestCase):
    def test_all_cooling_llm_backends_fail_fast_without_retry(self):
        gpu = FakeLLM(error="gpu failed")
        cpu = FakeLLM(error="cpu failed")
        router = AdaptiveLLM([("gpu-vulkan", gpu), ("cpu-native", cpu)], cooldown_seconds=60)
        with self.assertRaises(LLMUnavailable):
            router.chat([ChatMessage("user", "one")])
        self.assertEqual((gpu.calls, cpu.calls), (1, 1))
        with self.assertRaisesRegex(LLMUnavailable, "cooldown"):
            router.chat([ChatMessage("user", "two")])
        self.assertEqual((gpu.calls, cpu.calls), (1, 1))

    def test_openvino_http_503_is_quarantined_and_not_retried(self):
        npu = FakeLLM(error="HTTP 503 Service Unavailable")
        cpu = FakeLLM(answer="cpu")
        router = AdaptiveLLM([("npu-openvino", npu), ("cpu-native", cpu)], priority=("npu", "cpu", "gpu", "other"))
        self.assertEqual(router.chat([ChatMessage("user", "one")]), "cpu")
        self.assertEqual(router.chat([ChatMessage("user", "two")]), "cpu")
        self.assertEqual(npu.calls, 1)
        npu_status = next(row for row in router.status()["backends"] if row["kind"] == "npu")
        self.assertTrue(npu_status["quarantined"])

    def test_empty_reasoning_only_response_is_not_success(self):
        reasoning_only = FakeLLM(answer="<think>thinking</think>")
        healthy = FakeLLM(answer="Содержательный ответ")
        router = AdaptiveLLM([("gpu-vulkan", reasoning_only), ("cpu-native", healthy)])
        self.assertEqual(router.chat([ChatMessage("user", "Скажи что-нибудь")]), "Содержательный ответ")
        self.assertEqual((reasoning_only.calls, healthy.calls), (1, 1))

    def test_slow_backend_error_does_not_cascade_into_other_hardware(self):
        gpu = FakeLLM(error="timeout")
        cpu = FakeLLM(answer="cpu")
        router = AdaptiveLLM([("gpu-vulkan", gpu), ("cpu-native", cpu)])
        real_clock = time.monotonic
        # Контролируем только часы адаптивного маршрутизатора, без sleep(30).
        from unittest.mock import patch
        readings = iter([0.0, 0.0, 35.0, 35.0, 35.0, 35.0])
        with patch("leshy.adaptive_backend.time.monotonic", side_effect=lambda: next(readings, 35.0)):
            with self.assertRaises(LLMUnavailable):
                router.chat([ChatMessage("user", "Hello")])
        self.assertEqual(gpu.calls, 1)
        self.assertEqual(cpu.calls, 0)

    def test_embeddings_do_not_retry_during_cooldown(self):
        backend = FakeEmbeddings(fail=True)
        router = AdaptiveEmbeddings([("cpu-native", backend)], cooldown_seconds=60)
        with self.assertRaises(RuntimeError):
            router.embed("one")
        self.assertEqual(backend.calls, 1)
        with self.assertRaises(RuntimeError):
            router.embed("two")
        self.assertEqual(backend.calls, 1)
        self.assertFalse(router.available())


if __name__ == "__main__":
    unittest.main()
