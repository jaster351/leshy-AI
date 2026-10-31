from __future__ import annotations

import unittest

from leshy.adaptive_backend import AdaptiveEmbeddings, AdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable


class FakeLLM:
    def __init__(self, name: str, log: list[str], *, answer: str | None = None, error: str | None = None):
        self.name = name; self.log = log; self.answer = answer or name; self.error = error; self.calls = 0
    def chat(self, messages):
        self.calls += 1; self.log.append(self.name)
        if self.error: raise LLMUnavailable(self.error)
        return self.answer


class FakeEmbeddings:
    def __init__(self, name: str, log: list[str], *, fail: bool = False, error: str | None = None):
        self.name = name; self.log = log; self.fail = fail; self.error = error; self.calls = 0; self.model = f"{name}:test.gguf"
    def available(self): return True
    def embed(self, text):
        self.calls += 1; self.log.append(self.name)
        if self.error: raise RuntimeError(self.error)
        if self.fail: raise RuntimeError(f"{self.name} failed")
        return [0.1, 0.2, 0.3]


class GpuPriorityTests(unittest.TestCase):
    def test_gpu_is_always_first_when_healthy(self):
        log=[]; cpu=FakeLLM("cpu-native",log); npu=FakeLLM("npu-openvino",log); gpu=FakeLLM("gpu-vulkan",log,answer="gpu-answer")
        router=AdaptiveLLM([("cpu-native",cpu),("npu-openvino",npu),("gpu-vulkan",gpu)],cooldown_seconds=60)
        self.assertEqual(router.chat([ChatMessage("user","test")]),"gpu-answer"); self.assertEqual(log,["gpu-vulkan"])

    def test_gpu_failure_falls_back_to_npu_before_cpu(self):
        log=[]; gpu=FakeLLM("gpu-vulkan",log,error="HTTP 503"); npu=FakeLLM("npu-openvino",log,answer="npu-answer"); cpu=FakeLLM("cpu-native",log,answer="cpu-answer")
        router=AdaptiveLLM([("cpu-native",cpu),("npu-openvino",npu),("gpu-vulkan",gpu)],cooldown_seconds=60)
        self.assertEqual(router.chat([ChatMessage("user","test")]),"npu-answer"); self.assertEqual(log,["gpu-vulkan","npu-openvino"])

    def test_gpu_and_npu_failure_falls_back_to_cpu(self):
        log=[]; gpu=FakeLLM("gpu-vulkan",log,error="gpu failed"); npu=FakeLLM("npu-openvino",log,error="npu failed"); cpu=FakeLLM("cpu-native",log,answer="cpu-answer")
        router=AdaptiveLLM([("npu-openvino",npu),("cpu-native",cpu),("gpu-vulkan",gpu)],cooldown_seconds=60)
        self.assertEqual(router.chat([ChatMessage("user","test")]),"cpu-answer"); self.assertEqual(log,["gpu-vulkan","npu-openvino","cpu-native"])

    def test_background_llm_uses_npu_then_cpu_then_gpu(self):
        log=[]
        npu=FakeLLM("npu-openvino",log,error="temporary npu failure")
        cpu=FakeLLM("cpu-native",log,error="temporary cpu failure")
        gpu=FakeLLM("gpu-vulkan",log,answer="gpu-background")
        router=AdaptiveLLM(
            [("gpu-vulkan",gpu),("cpu-native",cpu),("npu-openvino",npu)],
            cooldown_seconds=60,
            priority=("npu","cpu","gpu","other"),
        )
        self.assertEqual(router.chat([ChatMessage("user","background")]),"gpu-background")
        self.assertEqual(log,["npu-openvino","cpu-native","gpu-vulkan"])

    def test_background_healthy_npu_does_not_touch_interactive_gpu(self):
        log=[]
        gpu=FakeLLM("gpu-vulkan",log,answer="gpu")
        npu=FakeLLM("npu-openvino",log,answer="npu-background")
        cpu=FakeLLM("cpu-native",log,answer="cpu")
        router=AdaptiveLLM(
            [("gpu-vulkan",gpu),("npu-openvino",npu),("cpu-native",cpu)],
            priority=("npu","cpu","gpu","other"),
        )
        self.assertEqual(router.chat([ChatMessage("user","background")]),"npu-background")
        self.assertEqual(log,["npu-openvino"])
        self.assertEqual(gpu.calls,0)

    def test_gpu_is_retried_after_cooldown(self):
        log=[]; gpu=FakeLLM("gpu-vulkan",log,error="temporary 503"); npu=FakeLLM("npu-openvino",log,answer="npu-answer"); cpu=FakeLLM("cpu-native",log,answer="cpu-answer")
        router=AdaptiveLLM([("gpu-vulkan",gpu),("npu-openvino",npu),("cpu-native",cpu)],cooldown_seconds=60)
        self.assertEqual(router.chat([ChatMessage("user","one")]),"npu-answer"); log.clear()
        self.assertEqual(router.chat([ChatMessage("user","two")]),"npu-answer"); self.assertEqual(log,["npu-openvino"])
        for state in router._states:
            if state.name=="gpu-vulkan": state.failed_until=0.0; state.provider.error=None; state.provider.answer="gpu-recovered"
        log.clear(); self.assertEqual(router.chat([ChatMessage("user","three")]),"gpu-recovered"); self.assertEqual(log,["gpu-vulkan"])

    def test_fatal_npu_memory_failure_is_not_retried_in_same_process(self):
        log=[]; npu=FakeLLM("npu-openvino",log,error="OpenVINO OUT_OF_HOST_MEMORY"); cpu=FakeLLM("cpu-native",log,answer="cpu-answer")
        router=AdaptiveLLM([("npu-openvino",npu),("cpu-native",cpu)],cooldown_seconds=5,priority=("npu","cpu","gpu","other"))
        self.assertEqual(router.chat([ChatMessage("user","one")]),"cpu-answer")
        self.assertEqual(router.chat([ChatMessage("user","two")]),"cpu-answer")
        self.assertEqual(npu.calls,1)
        status=router.status(); npu_status=next(x for x in status["backends"] if x["kind"]=="npu")
        self.assertTrue(npu_status["quarantined"]); self.assertIsNone(npu_status["cooldown_remaining_seconds"])

    def test_embeddings_use_gpu_then_npu_then_cpu(self):
        log=[]; gpu=FakeEmbeddings("gpu-vulkan",log,fail=True); npu=FakeEmbeddings("npu-openvino",log,fail=True); cpu=FakeEmbeddings("cpu-native",log)
        router=AdaptiveEmbeddings([("cpu-native",cpu),("npu-openvino",npu),("gpu-vulkan",gpu)],cooldown_seconds=60)
        self.assertEqual(router.embed("hello"),[0.1,0.2,0.3]); self.assertEqual(log,["gpu-vulkan","npu-openvino","cpu-native"])

    def test_fatal_npu_embedding_failure_is_quarantined(self):
        log=[]; npu=FakeEmbeddings("npu-openvino",log,error="OUT_OF_DEVICE_MEMORY"); cpu=FakeEmbeddings("cpu-native",log)
        router=AdaptiveEmbeddings([("npu-openvino",npu),("cpu-native",cpu)],priority=("npu","cpu","gpu","other"))
        self.assertEqual(router.embed("one"),[0.1,0.2,0.3]); self.assertEqual(router.embed("two"),[0.1,0.2,0.3]); self.assertEqual(npu.calls,1)


if __name__ == "__main__": unittest.main()
