"""Regression: slot execution duration is distinct from time spent waiting.

These tests are intentionally not executed by the autonomous development job.
"""
import threading
import time
import unittest

from leshy.inference_scheduler import InferenceScheduler, PRIORITY_BACKGROUND, PRIORITY_WEB


class InferenceActiveDurationTests(unittest.TestCase):
    def test_active_duration_starts_only_after_grant(self):
        scheduler = InferenceScheduler()
        first = scheduler.acquire(source="web", priority=PRIORITY_WEB)
        entered = threading.Event()
        granted = threading.Event()
        result = {}

        def worker():
            entered.set()
            with scheduler.slot(source="background", priority=PRIORITY_BACKGROUND, timeout=2):
                result["status"] = scheduler.status()
                granted.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            deadline = time.monotonic() + 1
            while scheduler.status()["queue_depth"] < 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(scheduler.status()["queue_depth"], 1)
            time.sleep(0.04)
        finally:
            scheduler.release(first)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(granted.is_set())
        self.assertEqual(result["status"]["active"]["source"], "background")
        self.assertLess(result["status"]["active"]["held_ms"], 35)

    def test_idle_status_has_no_active_duration(self):
        scheduler = InferenceScheduler()
        self.assertIsNone(scheduler.status()["active"])
        with scheduler.slot(source="web", priority=PRIORITY_WEB):
            active = scheduler.status()["active"]
            self.assertGreaterEqual(active["held_ms"], 0)
        self.assertIsNone(scheduler.status()["active"])


if __name__ == "__main__":
    unittest.main()
