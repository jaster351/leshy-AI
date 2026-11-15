from __future__ import annotations

import unittest

from leshy.adaptive_backend import AdaptiveLLM
from leshy.background_backend import BackgroundAdaptiveLLM
from leshy.llm import ChatMessage, LLMUnavailable


class _Provider:
    def __init__(self, name: str, calls: list[str], *, answer: str | None = None, fail: bool = False):
        self.name = name
        self.calls = calls
        self.answer = answer or name
        self.fail = fail

    def chat(self, messages):
        self.calls.append(self.name)
        if self.fail:
            raise LLMUnavailable(f"{self.name} failed")
        return self.answer


class BackgroundBackendTests(unittest.TestCase):
    def test_background_order_is_npu_cpu_gpu(self):
        calls: list[str] = []
        gpu = _Provider("gpu-vulkan", calls, answer="gpu")
        cpu = _Provider("cpu-native", calls, fail=True)
        npu = _Provider("npu-openvino", calls, fail=True)
        router = BackgroundAdaptiveLLM([
            ("gpu-vulkan", gpu),
            ("cpu-native", cpu),
            ("npu-openvino", npu),
        ])

        self.assertEqual(router.chat([ChatMessage("user", "background")]), "gpu")
        self.assertEqual(calls, ["npu-openvino", "cpu-native", "gpu-vulkan"])
        self.assertEqual(router.status()["effective_routing"], ["npu", "cpu", "gpu", "other"])

    def test_background_router_reuses_interactive_provider_objects(self):
        calls: list[str] = []
        gpu = _Provider("gpu-vulkan", calls)
        cpu = _Provider("cpu-native", calls)
        npu = _Provider("npu-openvino", calls, answer="npu")
        interactive = AdaptiveLLM([
            ("gpu-vulkan", gpu),
            ("npu-openvino", npu),
            ("cpu-native", cpu),
        ])

        background = BackgroundAdaptiveLLM.from_interactive(interactive)

        interactive_providers = {state.name: state.provider for state in interactive._states}
        background_providers = {state.name: state.provider for state in background._states}
        self.assertEqual(interactive_providers.keys(), background_providers.keys())
        for name in interactive_providers:
            self.assertIs(interactive_providers[name], background_providers[name])
        self.assertEqual(background.chat([ChatMessage("user", "background")]), "npu")
        self.assertEqual(calls, ["npu-openvino"])

    def test_interactive_hardware_mode_cannot_override_background_priority(self):
        calls: list[str] = []
        gpu = _Provider("gpu-vulkan", calls, answer="gpu")
        npu = _Provider("npu-openvino", calls, answer="npu")
        cpu = _Provider("cpu-native", calls, answer="cpu")
        router = BackgroundAdaptiveLLM([
            ("gpu-vulkan", gpu),
            ("npu-openvino", npu),
            ("cpu-native", cpu),
        ])

        # Regression: AdaptiveLLM normally consults hardware.json. The dedicated
        # background router must ignore that interactive preference.
        self.assertEqual(router._hardware_mode(), "auto")
        self.assertEqual(router.chat([ChatMessage("user", "background")]), "npu")
        self.assertEqual(calls, ["npu-openvino"])


if __name__ == "__main__":
    unittest.main()
