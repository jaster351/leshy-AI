"""Регрессионные проверки timeout=0."""
import threading
import unittest
from leshy.inference_scheduler import InferenceScheduler, PRIORITY_WEB

class ZeroTimeoutAcquireTests(unittest.TestCase):
    def test_free_slot(self):
        s = InferenceScheduler()
        token = s.acquire(source="web:instant", priority=PRIORITY_WEB, timeout=0)
        try:
            self.assertTrue(token.granted)
            self.assertEqual(s.status()["active"]["source"], "web:instant")
        finally:
            s.release(token)
        self.assertIsNone(s.status()["active"])

    def test_busy_slot(self):
        s = InferenceScheduler()
        with s.slot(source="web:busy", priority=PRIORITY_WEB):
            with self.assertRaises(TimeoutError):
                s.acquire(source="web:next", priority=PRIORITY_WEB, timeout=0)
            self.assertEqual(s.status()["queue_depth"], 0)

    def test_pre_cancelled(self):
        s = InferenceScheduler()
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            s.acquire(source="web:cancelled", priority=PRIORITY_WEB, timeout=0, cancel_event=cancelled)
        self.assertIsNone(s.status()["active"])
        self.assertEqual(s.status()["queue_depth"], 0)
