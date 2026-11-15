from __future__ import annotations

import unittest

from leshy.hardware import HardwareCapabilities, HardwareConfig, resolve_execution_plan


def caps(*, gpu: bool, npu: bool, gpu_count: int = 1) -> HardwareCapabilities:
    return HardwareCapabilities(
        cpu=True,
        cpu_name="Test CPU",
        gpu=gpu,
        gpu_names=tuple(f"GPU {index}" for index in range(gpu_count)) if gpu else (),
        gpu_offload_runtime=gpu,
        npu=npu,
        npu_names=("Test NPU",) if npu else (),
        openvino_npu=npu,
        notes=(),
    )


class HardwarePriorityTests(unittest.TestCase):
    def test_auto_with_gpu_and_npu_uses_gpu_for_chat(self):
        plan = resolve_execution_plan(HardwareConfig(mode="auto"), caps(gpu=True, npu=True, gpu_count=2))
        self.assertEqual(plan.effective_mode, "cpu_gpu_npu")
        self.assertEqual(plan.chat_device, "gpu")
        self.assertEqual(plan.chat_backend, "vulkan")
        self.assertEqual(plan.n_gpu_layers, -1)

    def test_auto_gpu_only_still_uses_gpu_for_chat(self):
        plan = resolve_execution_plan(HardwareConfig(mode="auto"), caps(gpu=True, npu=False, gpu_count=2))
        self.assertEqual(plan.effective_mode, "cpu_gpu")
        self.assertEqual(plan.chat_device, "gpu")
        self.assertEqual(plan.chat_backend, "vulkan")

    def test_auto_without_gpu_uses_npu_before_cpu(self):
        plan = resolve_execution_plan(HardwareConfig(mode="auto"), caps(gpu=False, npu=True))
        self.assertEqual(plan.effective_mode, "cpu_npu")
        self.assertEqual(plan.chat_device, "npu")
        self.assertEqual(plan.chat_backend, "openvino")

    def test_auto_without_accelerators_uses_cpu(self):
        plan = resolve_execution_plan(HardwareConfig(mode="auto"), caps(gpu=False, npu=False))
        self.assertEqual(plan.effective_mode, "cpu")
        self.assertEqual(plan.chat_device, "cpu")
        self.assertEqual(plan.chat_backend, "python-cpu")


if __name__ == "__main__":
    unittest.main()
