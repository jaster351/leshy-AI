"""Регрессии порядка ожидания inference (без запуска локальных тестов).

Проверяется только диспетчер слотов, не реальная отмена backend.
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


class InferencePriorityOrderingTests(unittest.TestCase):
    def test_waiting_web_then_telegram_then_background_fifo_within_priority(self):
        scheduler = InferenceScheduler()
        active = scheduler.acquire(source="active", priority=PRIORITY_BACKGROUND)
        order = []
        errors = []
        workers = []

        def request(source, priority):
            try:
                with scheduler.slot(source=source, priority=priority, timeout=3):
                    order.append(source)
            except Exception as exc:
                errors.append(exc)

        requests = [
            ("background", PRIORITY_BACKGROUND),
            ("telegram", PRIORITY_TELEGRAM),
            ("web-first", PRIORITY_WEB),
            ("web-second", PRIORITY_WEB),
        ]
        try:
            for index, (source, priority) in enumerate(requests, 1):
                thread = threading.Thread(target=request, args=(source, priority), daemon=True)
                workers.append(thread)
                thread.start()
                deadline = time.monotonic() + 1
                while scheduler.status()["queue_depth"] < index and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(scheduler.status()["queue_depth"], index)
            self.assertEqual(scheduler.status()["active"]["source"], "active")
        finally:
            scheduler.release(active)
            for thread in workers:
                thread.join(4)

        self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertFalse(errors, errors)
        self.assertEqual(order, ["web-first", "web-second", "telegram", "background"])
        self.assertIsNone(scheduler.status()["active"])
        self.assertEqual(scheduler.status()["queue_depth"], 0)


    def test_cancelled_highest_priority_waiter_does_not_block_next(self):
        scheduler = InferenceScheduler()
        active = scheduler.acquire(source="background-active", priority=PRIORITY_BACKGROUND)
        cancelled = threading.Event()
        order = []
        errors = []
        workers = []

        def request(source, priority, cancel_event=None):
            try:
                with scheduler.slot(
                    source=source, priority=priority,
                    cancel_event=cancel_event, timeout=3,
                ):
                    order.append(source)
            except InterruptedError:
                if source != "web-cancelled":
                    errors.append("unexpected cancellation: " + source)
            except Exception as exc:
                errors.append(exc)

        try:
            for source, priority, signal in [
                ("web-cancelled", PRIORITY_WEB, cancelled),
                ("telegram-survivor", PRIORITY_TELEGRAM, None),
            ]:
                worker = threading.Thread(
                    target=request, args=(source, priority, signal), daemon=True,
                )
                workers.append(worker)
                worker.start()
            deadline = time.monotonic() + 2
            while scheduler.status()["queue_depth"] != 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(scheduler.status()["queue_depth"], 2)
            cancelled.set()
            deadline = time.monotonic() + 2
            while scheduler.status()["queue_depth"] != 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(scheduler.status()["queue_depth"], 1)
            self.assertEqual(scheduler.status()["active"]["source"], "background-active")
        finally:
            scheduler.release(active)
            for worker in workers:
                worker.join(4)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertFalse(errors, errors)
        self.assertEqual(order, ["telegram-survivor"])
        self.assertEqual(scheduler.status()["queue_depth"], 0)
        self.assertIsNone(scheduler.status()["active"])



if __name__ == "__main__":
    unittest.main()
