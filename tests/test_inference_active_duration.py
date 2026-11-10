"""Regression: slot execution duration is distinct from time spent waiting.

These tests are intentionally not executed by the autonomous development job.
"""
import threading
import time
import unittest
from unittest.mock import patch

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

    def test_exception_releases_slot_and_next_request_can_run(self):
        scheduler = InferenceScheduler()
        with self.assertRaisesRegex(RuntimeError, "backend failed"):
            with scheduler.slot(source="background", priority=PRIORITY_BACKGROUND):
                self.assertEqual(scheduler.status()["active"]["source"], "background")
                raise RuntimeError("backend failed")
        self.assertIsNone(scheduler.status()["active"])
        self.assertEqual(scheduler.status()["queue_depth"], 0)
        with scheduler.slot(source="web", priority=PRIORITY_WEB, timeout=0.5):
            self.assertEqual(scheduler.status()["active"]["source"], "web")
        self.assertIsNone(scheduler.status()["active"])

    def test_web_waits_until_active_background_releases_without_false_preemption(self):
        scheduler = InferenceScheduler()
        background = scheduler.acquire(source="background", priority=PRIORITY_BACKGROUND)
        entered = threading.Event()
        granted = threading.Event()
        errors = []

        def web_request():
            entered.set()
            try:
                with scheduler.slot(source="web", priority=PRIORITY_WEB, timeout=2):
                    granted.set()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=web_request, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            deadline = time.monotonic() + 1
            while scheduler.status()["queue_depth"] == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            status = scheduler.status()
            self.assertEqual(status["active"]["source"], "background")
            self.assertEqual(status["waiting"][0]["source"], "web")
            self.assertFalse(granted.is_set())
            self.assertGreaterEqual(status["active"]["held_ms"], 0)
        finally:
            scheduler.release(background)
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(granted.is_set())
        self.assertIsNone(scheduler.status()["active"])

    def test_release_event_separates_queue_wait_from_held_time(self):
        scheduler = InferenceScheduler()
        recorded = []

        def capture(name, **fields):
            recorded.append((name, fields))

        # No backend is started: this verifies the metrics contract deterministically.
        with patch("leshy.inference_scheduler.event", side_effect=capture):
            with patch("leshy.inference_scheduler.time.monotonic",
                       side_effect=[10.0, 10.25, 10.25, 10.75]):
                waiter = scheduler.acquire(source="background", priority=PRIORITY_BACKGROUND)
                scheduler.release(waiter)

        release = [fields for name, fields in recorded if name == "inference_queue_released"]
        self.assertEqual(len(release), 1)
        self.assertEqual(release[0]["total_ms"], 750)
        self.assertEqual(release[0]["held_ms"], 500)
        self.assertEqual(release[0]["source"], "background")
        self.assertEqual(release[0]["queue_depth"], 0)
        self.assertIsNone(scheduler.status()["active"])

    def test_idle_status_has_no_active_duration(self):
        scheduler = InferenceScheduler()
        self.assertIsNone(scheduler.status()["active"])
        with scheduler.slot(source="web", priority=PRIORITY_WEB):
            active = scheduler.status()["active"]
            self.assertGreaterEqual(active["held_ms"], 0)
        self.assertIsNone(scheduler.status()["active"])


if __name__ == "__main__":
    unittest.main()
