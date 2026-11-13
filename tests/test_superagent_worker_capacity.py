"""Adaptive worker admission: scale with resources, not a fixed two-PC/8-thread limit."""
import unittest

from leshy.superagent_worker_capacity import worker_ceiling, available_worker_slots


MIB = 1024 * 1024


class WorkerCapacityTests(unittest.TestCase):
    def test_cpu_scales_without_eight_worker_ceiling(self):
        self.assertEqual(worker_ceiling("auto", cpu_count=16), 32)
        self.assertEqual(worker_ceiling("auto", cpu_count=128), 64)
        self.assertEqual(worker_ceiling("auto", cpu_count=1), 2)

    def test_explicit_override_is_per_node_and_safely_bounded(self):
        self.assertEqual(worker_ceiling("24", cpu_count=4), 24)
        self.assertEqual(worker_ceiling("1000", cpu_count=8), 128)
        self.assertEqual(worker_ceiling("bad", cpu_count=8), 16)
        self.assertEqual(worker_ceiling("0", cpu_count=8), 16)

    def test_memory_pressure_blocks_only_new_admissions(self):
        self.assertEqual(available_worker_slots(32, free_memory_bytes=512 * MIB), 0)
        self.assertEqual(available_worker_slots(32, free_memory_bytes=1024 * MIB), 2)
        self.assertEqual(available_worker_slots(32, free_memory_bytes=8 * 1024 * MIB), 30)
        self.assertEqual(available_worker_slots(32, free_memory_bytes=32 * 1024 * MIB), 32)

    def test_unknown_ram_falls_back_to_configured_cpu_ceiling(self):
        self.assertEqual(available_worker_slots(40, memory_probe=lambda: None), 40)
        self.assertEqual(available_worker_slots(4, memory_probe=lambda: 0), 0)

    def test_invalid_requests_cannot_overflow_worker_pool(self):
        for ceiling in (0, 129, -1, True):
            with self.subTest(ceiling=ceiling):
                with self.assertRaises(ValueError):
                    available_worker_slots(ceiling, free_memory_bytes=1024 * MIB)


if __name__ == "__main__":
    unittest.main()
