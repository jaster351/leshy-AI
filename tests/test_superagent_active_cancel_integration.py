"""Контракт активной фоновой операции: сигнал stop не освобождает чужой GPU slot.

Регрессия для реальной обёртки SuperAgent, без запуска backend или локальных тестов.
"""
import threading
import unittest
from unittest.mock import patch

from leshy.inference_scheduler import InferenceScheduler, PRIORITY_WEB
from leshy import superagent_scheduler as integration


class SuperAgentActiveCancellationTests(unittest.TestCase):
    def test_stop_during_execute_keeps_slot_until_backend_returns(self):
        scheduler = InferenceScheduler()
        started = threading.Event()
        finish = threading.Event()
        completed = threading.Event()
        errors = []

        class FakeService:
            def __init__(self):
                self.app = object()
                self._stop = threading.Event()

            def _execute(self, ticket):
                started.set()
                if not finish.wait(5):
                    raise TimeoutError("synthetic backend did not finish")
                return ticket["id"]

        original_method = FakeService._execute
        installed = integration._INSTALLED
        original_execute = integration._ORIGINAL_EXECUTE
        try:
            with patch.object(integration, "SuperAgentService", FakeService), \\
                 patch.object(integration, "get_inference_scheduler", return_value=scheduler), \\
                 patch.object(integration, "event"):
                integration._INSTALLED = False
                integration._ORIGINAL_EXECUTE = None
                integration.install_superagent_background_scheduler()
                service = FakeService()

                def run():
                    try:
                        self.assertEqual(service._execute({"id": "slow"}), "slow")
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        completed.set()

                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                try:
                    self.assertTrue(started.wait(5), "background never acquired slot")
                    service._stop.set()
                    self.assertEqual(scheduler.status()["active"]["source"], "superagent:slow")
                    with self.assertRaises(TimeoutError):
                        scheduler.acquire(source="web:waiting", priority=PRIORITY_WEB, timeout=0)
                    self.assertEqual(scheduler.status()["active"]["source"], "superagent:slow")
                    self.assertFalse(completed.is_set())
                finally:
                    finish.set()
                    worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertFalse(errors, errors)
                self.assertIsNone(scheduler.status()["active"])
                self.assertEqual(scheduler.status()["queue_depth"], 0)
                with scheduler.slot(source="web:after", priority=PRIORITY_WEB, timeout=0):
                    self.assertEqual(scheduler.status()["active"]["source"], "web:after")
        finally:
            FakeService._execute = original_method
            integration._INSTALLED = installed
            integration._ORIGINAL_EXECUTE = original_execute


if __name__ == "__main__":
    unittest.main()
