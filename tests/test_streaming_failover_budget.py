"""Ограничение потокового fallback при медленных и пустых ответах."""
from __future__ import annotations
import threading
import unittest
from unittest.mock import patch
from leshy.adaptive_backend import AdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable
from leshy.streaming_backends import _adaptive_stream


class FakeStream:
    def __init__(self, parts=(), exception=None):
        self.parts = list(parts)
        self.exception = exception
        self.calls = 0
        self.n_ctx = 4096
        self.max_tokens = 512

    def stream_chat(self, messages, *, cancel_event=None):
        self.calls += 1
        if self.exception:
            raise self.exception
        yield from self.parts


class AdaptiveStreamTests(unittest.TestCase):
    def test_empty_stream_is_failure_and_fast_failover_works(self):
        gpu = FakeStream(parts=[])
        cpu = FakeStream(parts=["ответ"])
        router = AdaptiveLLM([("gpu-vulkan", gpu), ("cpu-native", cpu)])
        self.assertEqual("".join(_adaptive_stream(router, [ChatMessage("user", "Привет")])), "ответ")
        self.assertEqual((gpu.calls, cpu.calls), (1, 1))

    def test_late_gpu_failure_recovers_via_single_cpu_without_npu_cascade(self):
        gpu = FakeStream(exception=RuntimeError("timeout"))
        npu = FakeStream(exception=RuntimeError("slow startup"))
        cpu = FakeStream(parts=["cpu"])
        router = AdaptiveLLM([
            ("gpu-vulkan", gpu), ("npu-openvino", npu), ("cpu-native", cpu),
        ])
        # Контролируем часы без запуска дорогих моделей.
        ticks = iter([0.0, 0.0, 35.0, 35.0, 35.0, 35.0, 35.0, 35.0, 35.0])
        with patch("leshy.streaming_backends.time.monotonic", side_effect=lambda: next(ticks, 35.0)):
            self.assertEqual("".join(_adaptive_stream(router,[ChatMessage("user","Привет")])), "cpu")
        self.assertEqual((gpu.calls,npu.calls,cpu.calls),(1,0,1))

    def test_partial_answer_never_merges_with_new_model(self):
        class Partial(FakeStream):
            def stream_chat(self, messages, *, cancel_event=None):
                self.calls += 1
                yield "первые токены"
                raise RuntimeError("backend died")
        gpu = Partial()
        cpu = FakeStream(parts=["другой ответ"])
        router = AdaptiveLLM([("gpu-vulkan", gpu), ("cpu-native", cpu)])
        with self.assertRaises(RuntimeError):
            "".join(_adaptive_stream(router, [ChatMessage("user", "Hello")]))
        self.assertEqual(cpu.calls, 0)

    def test_stream_context_error_does_not_quarantine_gpu(self):
        gpu = FakeStream(parts=["ok"])
        router = AdaptiveLLM([("gpu-vulkan", gpu)])
        with patch.object(AdaptiveLLM, "_budget_for", side_effect=LLMUnavailable("контекст превышен")):
            with self.assertRaisesRegex(LLMUnavailable, "контекст"):
                list(_adaptive_stream(router, [ChatMessage("user", "много текста")]))
        self.assertEqual(gpu.calls, 0)
        self.assertEqual(router.status()["backends"][0]["failures"], 0)

    def test_stream_budget_is_compacted_before_backend(self):
        class Capture(FakeStream):
            def stream_chat(self, messages, *, cancel_event=None):
                self.calls += 1
                self.seen = messages
                yield "ok"
        gpu = Capture()
        gpu.n_ctx = 1024
        gpu.max_tokens = 512
        router = AdaptiveLLM([("gpu-vulkan", gpu)])
        text = "русский " * 3000
        self.assertEqual("".join(_adaptive_stream(router, [ChatMessage("user", text)])), "ok")
        from leshy.context_budget import estimate_tokens
        self.assertLessEqual(estimate_tokens(gpu.seen), 512)


if __name__ == "__main__":
    unittest.main()
