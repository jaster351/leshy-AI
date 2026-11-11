"""Контракт отмены уже активной фоновой операции.

Тест не запускает LLM и не имитирует фактическую отмену backend.
Он фиксирует запрет преждевременного освобождения слота при cancel_event.
Локально не запускать без отдельного разрешения владельца.
"""
import threading
import unittest

from leshy.inference_scheduler import (
    InferenceScheduler,
    PRIORITY_BACKGROUND,
    PRIORITY_WEB,
)


class ActiveCancellationContractTests(unittest.TestCase):
    def test_cancelling_active_background_does_not_grant_web_until_backend_exits(self):
        scheduler = InferenceScheduler()
        cancel = threading.Event()
        background_entered = threading.Event()
        allow_background_exit = threading.Event()
        web_entered = threading.Event()
        errors = []

        def background():
            try:
                with scheduler.slot(
                    source="background-active",
                    priority=PRIORITY_BACKGROUND,
                    cancel_event=cancel,
                ):
                    background_entered.set()
                    if not allow_background_exit.wait(3):
                        raise AssertionError("background exit was not released")
            except Exception as exc:
                errors.append(exc)

        def web():
            try:
                with scheduler.slot(source="web-waiter", priority=PRIORITY_WEB, timeout=3):
                    web_entered.set()
            except Exception as exc:
                errors.append(exc)

        background_thread = threading.Thread(target=background, daemon=True)
        web_thread = threading.Thread(target=web, daemon=True)
        try:
            background_thread.start()
            self.assertTrue(background_entered.wait(1))
            web_thread.start()
            # Synchronize using the scheduler condition rather than sleep-based ordering.
            with scheduler._condition:
                self.assertTrue(
                    scheduler._condition.wait_for(
                        lambda: scheduler.status()["queue_depth"] == 1, timeout=1
                    )
                )
            cancel.set()
            state = scheduler.status()
            self.assertEqual(state["active"]["source"], "background-active")
            self.assertEqual(state["waiting"][0]["source"], "web-waiter")
            self.assertFalse(web_entered.is_set())
        finally:
            allow_background_exit.set()
            background_thread.join(4)
            web_thread.join(4)

        self.assertFalse(background_thread.is_alive())
        self.assertFalse(web_thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(web_entered.is_set())
        self.assertIsNone(scheduler.status()["active"])
        self.assertEqual(scheduler.status()["queue_depth"], 0)


if __name__ == "__main__":
    unittest.main()
