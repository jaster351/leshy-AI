"""Контракт приоритетов: ожидающий интерактивный чат опережает фон, FIFO внутри уровня.

Тест не запускает inference backend и не подтверждает preemption активной генерации.
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


class InteractivePriorityContractTests(unittest.TestCase):
    def test_web_then_telegram_then_background_after_active_release(self):
        scheduler = InferenceScheduler()
        blocker = scheduler.acquire(source="existing", priority=PRIORITY_BACKGROUND)
        order = []
        failures = []
        threads = []

        def worker(source, priority):
            try:
                with scheduler.slot(source=source, priority=priority, timeout=3):
                    order.append(source)
            except Exception as exc:
                failures.append(exc)

        try:
            for source, priority in (
                ("background", PRIORITY_BACKGROUND),
                ("telegram", PRIORITY_TELEGRAM),
                ("web", PRIORITY_WEB),
            ):
                thread = threading.Thread(target=worker, args=(source, priority), daemon=True)
                threads.append(thread)
                thread.start()
                deadline = time.monotonic() + 2
                while scheduler.status()["queue_depth"] < len(threads) and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(scheduler.status()["queue_depth"], len(threads))
            self.assertEqual(scheduler.status()["active"]["source"], "existing")
        finally:
            scheduler.release(blocker)
        for thread in threads:
            thread.join(timeout=4)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertFalse(failures, failures)
        self.assertEqual(order, ["web", "telegram", "background"])
        self.assertEqual(scheduler.status()["queue_depth"], 0)
        self.assertIsNone(scheduler.status()["active"])

    def test_equal_priority_keeps_fifo(self):
        scheduler = InferenceScheduler()
        blocker = scheduler.acquire(source="existing", priority=PRIORITY_BACKGROUND)
        order = []
        threads = []
        try:
            for index in range(3):
                thread = threading.Thread(
                    target=lambda i=index: self._run_waiter(scheduler, f"web-{i}", order),
                    daemon=True,
                )
                threads.append(thread)
                thread.start()
                deadline = time.monotonic() + 2
                while scheduler.status()["queue_depth"] < len(threads) and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(scheduler.status()["queue_depth"], len(threads))
        finally:
            scheduler.release(blocker)
        for thread in threads:
            thread.join(timeout=4)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(order, ["web-0", "web-1", "web-2"])

    @staticmethod
    def _run_waiter(scheduler, source, order):
        with scheduler.slot(source=source, priority=PRIORITY_WEB, timeout=3):
            order.append(source)


if __name__ == "__main__":
    unittest.main()
