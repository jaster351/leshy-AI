from __future__ import annotations

import unittest

from leshy.adaptive_backend import AdaptiveEmbeddings, AdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable


class FakeLLM:
    def __init__(self, name: str, log: list[str], *, answer: str | None = None, error: str | None = None):
        self.name = name
        self.log = log
        self.answer = answer or name
        self.error = error
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        self.log.append(self.name)
        if self.error:
            raise LLMUnavailable(self.error)
        return self.answer


class FakeEmbeddings:
    def __init__(self, name: str, log: list[str], *, fail: bool = False):
        self.name = name
        self.log = log
        self.fail = fail
        self.calls = 0
        self.model = f"{name}:test.gguf"

    def available(self):
        return True

    def embed(self, text):
        self.calls += 1
        self.log.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return [0.1, 0.2, 0.3]


class GpuPriorityTests(unittest.TestCase):
    def test_gpu_is_always_first_when_healthy(self):
        log: list[str] = []
        cpu = FakeLLM("cpu-native", log)
        npu = FakeLLM("npu-openvino", log)
        gpu = FakeLLM("gpu-vulkan", log, answer="gpu-answer")
        router = AdaptiveLLM(
            [("cpu-native", cpu), ("npu-openvino", npu), ("gpu-vulkan", gpu)],
            cooldown_seconds=60,
        )

        self.assertEqual(router.chat([ChatMessage("user", "test")]), "gpu-answer")
        self.assertEqual(log, ["gpu-vulkan"])
        self.assertEqual(gpu.calls, 1)
        self.assertEqual(npu.calls, 0)
        self.assertEqual(cpu.calls, 0)

    def test_gpu_failure_falls_back_to_npu_before_cpu(self):
        log: list[str] = []
        gpu = FakeLLM("gpu-vulkan", log, error="HTTP 503")
        npu = FakeLLM("npu-openvino", log, answer="npu-answer")
        cpu = FakeLLM("cpu-native", log, answer="cpu-answer")
        router = AdaptiveLLM(
            [("cpu-native", cpu), ("npu-openvino", npu), ("gpu-vulkan", gpu)],
            cooldown_seconds=60,
        )

        self.assertEqual(router.chat([ChatMessage("user", "test")]), "npu-answer")
        self.assertEqual(log, ["gpu-vulkan", "npu-openvino"])
        self.assertEqual(cpu.calls, 0)

    def test_gpu_and_npu_failure_falls_back_to_cpu(self):
        log: list[str] = []
        gpu = FakeLLM("gpu-vulkan", log, error="gpu failed")
        npu = FakeLLM("npu-openvino", log, error="npu failed")
        cpu = FakeLLM("cpu-native", log, answer="cpu-answer")
        router = AdaptiveLLM(
            [("npu-openvino", npu), ("cpu-native", cpu), ("gpu-vulkan", gpu)],
            cooldown_seconds=60,
        )

        self.assertEqual(router.chat([ChatMessage("user", "test")]), "cpu-answer")
        self.assertEqual(log, ["gpu-vulkan", "npu-openvino", "cpu-native"])

    def test_gpu_is_retried_after_cooldown(self):
        log: list[str] = []
        gpu = FakeLLM("gpu-vulkan", log, error="temporary 503")
        npu = FakeLLM("npu-openvino", log, answer="npu-answer")
        cpu = FakeLLM("cpu-native", log, answer="cpu-answer")
        router = AdaptiveLLM(
            [("gpu-vulkan", gpu), ("npu-openvino", npu), ("cpu-native", cpu)],
            cooldown_seconds=60,
        )

        self.assertEqual(router.chat([ChatMessage("user", "one")]), "npu-answer")
        self.assertEqual(log, ["gpu-vulkan", "npu-openvino"])

        log.clear()
        self.assertEqual(router.chat([ChatMessage("user", "two")]), "npu-answer")
        self.assertEqual(log, ["npu-openvino"])

        for state in router._states:
            if state.name == "gpu-vulkan":
                state.failed_until = 0.0
                state.provider.error = None
                state.provider.answer = "gpu-recovered"

        log.clear()
        self.assertEqual(router.chat([ChatMessage("user", "three")]), "gpu-recovered")
        self.assertEqual(log, ["gpu-vulkan"])

    def test_embeddings_use_gpu_then_npu_then_cpu(self):
        log: list[str] = []
        gpu = FakeEmbeddings("gpu-vulkan", log, fail=True)
        npu = FakeEmbeddings("npu-openvino", log, fail=True)
        cpu = FakeEmbeddings("cpu-native", log)
        router = AdaptiveEmbeddings(
            [("cpu-native", cpu), ("npu-openvino", npu), ("gpu-vulkan", gpu)],
            cooldown_seconds=60,
        )

        self.assertEqual(router.embed("hello"), [0.1, 0.2, 0.3])
        self.assertEqual(log, ["gpu-vulkan", "npu-openvino", "cpu-native"])


if __name__ == "__main__":
    unittest.main()
