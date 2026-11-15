"""The background inference slot wraps model generation, not the entire job."""
from __future__ import annotations

from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

from leshy.superagent import SuperAgentService
from leshy import superagent_scheduler


class FakeScheduler:
    def __init__(self):
        self.history = []

    @contextmanager
    def slot(self, **kwargs):
        self.history.append(("acquired", kwargs))
        try:
            yield
        finally:
            self.history.append(("released", kwargs))


class InferenceSchedulerScopeTests(unittest.TestCase):
    def test_installed_wrapper_is_per_call_and_idempotent(self):
        scheduler = FakeScheduler()
        service = object.__new__(SuperAgentService)
        service.app = object()
        service._stop = threading.Event()
        service._ticket_working = lambda _: True
        calls = []

        def original(self, ticket_id, messages):
            calls.append((ticket_id, tuple(messages)))
            return "llm-response"

        with (
            patch.object(superagent_scheduler, "_INSTALLED", False),
            patch.object(superagent_scheduler, "_ORIGINAL_CHAT", None),
            patch.object(SuperAgentService, "_chat_inference", original),
            patch.object(superagent_scheduler, "get_inference_scheduler", return_value=scheduler),
            patch.object(superagent_scheduler, "event"),
        ):
            superagent_scheduler.install_superagent_background_scheduler()
            superagent_scheduler.install_superagent_background_scheduler()
            self.assertEqual(service._chat_inference("SA-first", ["a"]), "llm-response")
            self.assertEqual(service._chat_inference("SA-second", ["b"]), "llm-response")

        self.assertEqual(calls, [("SA-first", ("a",)), ("SA-second", ("b",))])
        self.assertEqual([kind for kind, _ in scheduler.history],
                         ["acquired", "released", "acquired", "released"])
        first = scheduler.history[0][1]
        self.assertEqual(first["source"], "superagent:SA-first")
        self.assertEqual(first["priority"], superagent_scheduler.PRIORITY_BACKGROUND)
        self.assertFalse(first["cancel_event"].is_set())
        service._ticket_working = lambda _: False
        self.assertTrue(first["cancel_event"].is_set())
        self.assertIsNone(first["timeout"])


if __name__ == "__main__":
    unittest.main()
