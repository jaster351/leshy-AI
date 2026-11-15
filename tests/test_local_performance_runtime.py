from __future__ import annotations

import unittest

from leshy.performance_runtime import choose_llama_profile


class LocalPerformanceProfileTests(unittest.TestCase):
    def test_hybrid_cpu_20_threads_gpu_uses_eight(self):
        p = choose_llama_profile("vulkan", logical_cpus=20, env={})
        self.assertEqual((p.threads, p.batch_threads), (8, 8))
        self.assertEqual(p.mode, "auto")

    def test_cpu_fallback_remains_unmodified(self):
        p = choose_llama_profile("cpu", logical_cpus=20, env={})
        self.assertEqual((p.threads, p.batch_threads), (20, 20))

    def test_npu_profile_is_not_modified(self):
        self.assertEqual(choose_llama_profile("openvino", logical_cpus=20, env={}).threads, 20)

    def test_compat_restores_previous_behavior(self):
        p = choose_llama_profile("vulkan", logical_cpus=20, env={"LESHY_PERFORMANCE_PROFILE": "compat"})
        self.assertEqual((p.threads, p.batch_threads), (20, 20))

    def test_explicit_gpu_override(self):
        p = choose_llama_profile("vulkan", logical_cpus=20, env={"LESHY_LLAMA_GPU_THREADS": "12", "LESHY_LLAMA_BATCH_THREADS": "6"})
        self.assertEqual((p.threads, p.batch_threads), (12, 6))

    def test_override_is_bounded_by_available_cpus(self):
        p = choose_llama_profile("vulkan", logical_cpus=4, env={"LESHY_LLAMA_GPU_THREADS": "999"})
        self.assertEqual(p.threads, 4)

    def test_invalid_override_uses_auto_profile(self):
        p = choose_llama_profile("vulkan", logical_cpus=20, env={"LESHY_LLAMA_GPU_THREADS": "not-an-int"})
        self.assertEqual(p.threads, 8)

    def test_single_cpu_is_supported(self):
        self.assertEqual(choose_llama_profile("vulkan", logical_cpus=1, env={}).threads, 1)

    def test_profile_contains_only_local_settings(self):
        keys = set(choose_llama_profile("vulkan", logical_cpus=20, env={}).as_dict())
        self.assertEqual(keys, {"backend", "mode", "logical_cpus", "threads", "batch_threads"})
        self.assertNotIn("cluster", keys)


if __name__ == "__main__":
    unittest.main()
