"""Stopping one SuperAgent ticket revokes only its queued inference request."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest

from leshy.inference_scheduler import (
    InferenceScheduler, PRIORITY_BACKGROUND, PRIORITY_WEB,
)
from leshy.superagent_scheduler import _TicketCancellation


class FakeService:
    def __init__(self):
        self._stop = threading.Event()
        self.active = {"SA-a": True, "SA-b": True}

    def _ticket_working(self, ticket_id):
        return self.active.get(ticket_id, False)


class TicketInferenceCancellationTests(unittest.TestCase):
    def test_stop_specific_ticket_while_web_holds_model_slot(self):
        service = FakeService()
        scheduler = InferenceScheduler()
        with ThreadPoolExecutor(max_workers=2) as pool:
            with scheduler.slot(source="web", priority=PRIORITY_WEB):
                def background(ticket_id):
                    with scheduler.slot(
                        source="superagent:" + ticket_id,
                        priority=PRIORITY_BACKGROUND,
                        cancel_event=_TicketCancellation(service, ticket_id),
                        timeout=3,
                    ):
                        return "generated"

                cancelled = pool.submit(background, "SA-a")
                deadline = time.monotonic() + 1
                while scheduler.status()["queue_depth"] < 1 and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(scheduler.status()["queue_depth"], 1)
                service.active["SA-a"] = False
                with self.assertRaises(InterruptedError):
                    cancelled.result(timeout=2)
                self.assertEqual(scheduler.status()["queue_depth"], 0)
                surviving = pool.submit(background, "SA-b")
            self.assertEqual(surviving.result(timeout=2), "generated")

    def test_global_shutdown_or_owner_stop_revokes_inference_wait(self):
        service = FakeService()
        check = _TicketCancellation(service, "SA-a")
        self.assertFalse(check.is_set())
        service.active["SA-a"] = False
        self.assertTrue(check.is_set())
        service.active["SA-a"] = True
        self.assertFalse(check.is_set())
        service._stop.set()
        self.assertTrue(check.is_set())


if __name__ == "__main__":
    unittest.main()
