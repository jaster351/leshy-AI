"""Регрессии неблокирующего захвата inference-слота (timeout=0)."""
import unittest

from leshy.inference_scheduler import InferenceScheduler, PRIORITY_WEB, PRIORITY_BACKGROUND


class NonblockingAcquireTests(unittest.TestCase):
    def test_zero_timeout_grants_idle_slot(self):
        scheduler = InferenceScheduler()
        with scheduler.slot(source="web:immediate", priority=PRIORITY_WEB, timeout=0):
            self.assertEqual(scheduler.status()["active"]["source"], "web:immediate")
        self.assertIsNone(scheduler.status()["active"])
        self.assertEqual(scheduler.status()["queue_depth"], 0)

    def test_zero_timeout_does_not_steal_busy_slot(self):
        scheduler = InferenceScheduler()
        with scheduler.slot(source="background:busy", priority=PRIORITY_BACKGROUND):
            with self.assertRaises(TimeoutError):
                scheduler.acquire(source="web:nonblocking", priority=PRIORITY_WEB, timeout=0)
            self.assertEqual(scheduler.status()["active"]["source"], "background:busy")
            self.assertEqual(scheduler.status()["queue_depth"], 0)
        with scheduler.slot(source="web:after", priority=PRIORITY_WEB, timeout=0):
            self.assertEqual(scheduler.status()["active"]["source"], "web:after")


if __name__ == "__main__":
    unittest.main()
