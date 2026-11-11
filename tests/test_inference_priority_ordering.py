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


if __name__ == "__main__":
    unittest.main()
