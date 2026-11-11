"""Регрессии timeout/cleanup очереди inference.

Сценарии не прерывают активную генерацию и не требуют запуска backend.
Автоматическая задача не запускает тесты без разрешения владельца.
"""
import threading
import time
import unittest

from leshy.inference_scheduler import (
    InferenceScheduler,
    PRIORITY_BACKGROUND,
    PRIORITY_TELEGRAM,
    PRIORITY_WEB,
)


class InferenceTimeoutRecoveryTests(unittest.TestCase):
    def test_timed_out_web_waiter_is_removed_and_telegram_can_proceed(self):
        scheduler = InferenceScheduler()
        background = scheduler.acquire(source="background-active", priority=PRIORITY_BACKGROUND)
        timed_out = threading.Event()
        errors = []
        acquired = []

        def web():
            try:
                with scheduler.slot(source="web-timeout", priority=PRIORITY_WEB, timeout=0.15):
                    errors.append("timed-out web unexpectedly acquired")
            except TimeoutError:
                timed_out.set()
            except Exception as exc:
                errors.append(exc)

        def telegram():
            try:
                with scheduler.slot(source="telegram", priority=PRIORITY_TELEGRAM, timeout=2):
                    acquired.append("telegram")
            except Exception as exc:
                errors.append(exc)

        web_thread = threading.Thread(target=web, daemon=True)
        telegram_thread = threading.Thread(target=telegram, daemon=True)
        try:
            web_thread.start()
            self.assertTrue(timed_out.wait(1), "web timeout did not fire")
            self.assertEqual(scheduler.status()["queue_depth"], 0)
            self.assertEqual(scheduler.status()["active"]["source"], "background-active")
            telegram_thread.start()
            deadline = time.monotonic() + 1
            while scheduler.status()["queue_depth"] == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(scheduler.status()["waiting"][0]["source"], "telegram")
        finally:
            scheduler.release(background)
            web_thread.join(3)
            telegram_thread.join(3)

        self.assertFalse(web_thread.is_alive())
        self.assertFalse(telegram_thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(acquired, ["telegram"])
        self.assertEqual(scheduler.status()["queue_depth"], 0)
        self.assertIsNone(scheduler.status()["active"])

    def test_pre_cancelled_waiter_never_steals_active_slot(self):
        scheduler = InferenceScheduler()
        active = scheduler.acquire(source="background-active", priority=PRIORITY_BACKGROUND)
        cancelled = threading.Event()
        cancelled.set()
        try:
            with self.assertRaises(InterruptedError):
                scheduler.acquire(
                    source="web-already-cancelled",
                    priority=PRIORITY_WEB,
                    cancel_event=cancelled,
                    timeout=0.5,
                )
            state = scheduler.status()
            self.assertEqual(state["active"]["source"], "background-active")
            self.assertEqual(state["queue_depth"], 0)
        finally:
            scheduler.release(active)
        self.assertIsNone(scheduler.status()["active"])

    def test_stale_release_cannot_unlock_another_active_owner(self):
        scheduler = InferenceScheduler()
        first = scheduler.acquire(source="first", priority=PRIORITY_BACKGROUND)
        scheduler.release(first)
        second = scheduler.acquire(source="second", priority=PRIORITY_WEB)
        try:
            scheduler.release(first)
            self.assertEqual(scheduler.status()["active"]["source"], "second")
        finally:
            scheduler.release(second)
        self.assertIsNone(scheduler.status()["active"])


if __name__ == "__main__":
    unittest.main()
