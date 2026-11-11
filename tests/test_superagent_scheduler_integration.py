"""Статические регрессионные контракты обёртки SuperAgent → inference scheduler.

Тесты не запускаются автоматически при публикации файла.
"""
import threading
import unittest
from unittest.mock import patch

from leshy.inference_scheduler import InferenceScheduler, PRIORITY_BACKGROUND
from leshy import superagent_scheduler as integration


class SuperAgentSchedulerIntegrationTests(unittest.TestCase):
    def test_background_execute_holds_slot_until_original_returns(self):
        scheduler = InferenceScheduler()
        observed = []

        class FakeService:
            def __init__(self):
                self.app = object()
                self._stop = threading.Event()

            def _execute(self, ticket):
                observed.append(scheduler.status()["active"])
                return ticket["id"]

        original_method = FakeService._execute
        original_installed = integration._INSTALLED
        original_execute = integration._ORIGINAL_EXECUTE
        try:
            with patch.object(integration, "SuperAgentService", FakeService), \
                 patch.object(integration, "get_inference_scheduler", return_value=scheduler), \
                 patch.object(integration, "event"):
                integration._INSTALLED = False
                integration._ORIGINAL_EXECUTE = None
                integration.install_superagent_background_scheduler()
                self.assertEqual(FakeService()._execute({"id": "ticket-1"}), "ticket-1")
                self.assertEqual(observed[0]["source"], "superagent:ticket-1")
                self.assertEqual(observed[0]["priority"], PRIORITY_BACKGROUND)
                self.assertIsNone(scheduler.status()["active"])
                self.assertEqual(scheduler.status()["queue_depth"], 0)
        finally:
            FakeService._execute = original_method
            integration._INSTALLED = original_installed
            integration._ORIGINAL_EXECUTE = original_execute

    def test_background_exception_releases_slot(self):
        scheduler = InferenceScheduler()

        class FakeService:
            def __init__(self):
                self.app = object()
                self._stop = threading.Event()

            def _execute(self, ticket):
                self.active_during_call = scheduler.status()["active"]
                raise RuntimeError("backend unavailable")

        original_installed = integration._INSTALLED
        original_execute = integration._ORIGINAL_EXECUTE
        original_method = FakeService._execute
        try:
            with patch.object(integration, "SuperAgentService", FakeService), \
                 patch.object(integration, "get_inference_scheduler", return_value=scheduler), \
                 patch.object(integration, "event"):
                integration._INSTALLED = False
                integration._ORIGINAL_EXECUTE = None
                integration.install_superagent_background_scheduler()
                service = FakeService()
                with self.assertRaisesRegex(RuntimeError, "backend unavailable"):
                    service._execute({"id": "ticket-2"})
                self.assertEqual(service.active_during_call["source"], "superagent:ticket-2")
                self.assertIsNone(scheduler.status()["active"])
                self.assertEqual(scheduler.status()["queue_depth"], 0)
        finally:
            FakeService._execute = original_method
            integration._INSTALLED = original_installed
            integration._ORIGINAL_EXECUTE = original_execute


if __name__ == "__main__":
    unittest.main()
