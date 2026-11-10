"""Регрессии приоритетного слота LLM; тесты не запускаются при подготовке PR."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from leshy.inference_scheduler import (
    InferenceScheduler,
    PRIORITY_BACKGROUND,
    PRIORITY_TELEGRAM,
    PRIORITY_WEB,
)


class InferenceSchedulerPriorityRegression(unittest.TestCase):
    def setUp(self):
        self.events = patch("leshy.inference_scheduler.event")
        self.events.start()
        self.addCleanup(self.events.stop)
        self.scheduler = InferenceScheduler()

    def _wait_for_depth(self, depth: int) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self.scheduler.status()["queue_depth"] >= depth:
                return
            time.sleep(0.005)
        self.fail(f"Очередь не достигла глубины {depth}")

    def test_web_precedes_background_when_both_are_queued(self):
        holder = self.scheduler.acquire(source="holder", priority=PRIORITY_BACKGROUND)
        order = []
        errors = []

        def worker(name, priority):
            try:
                with self.scheduler.slot(source=name, priority=priority, timeout=2):
                    order.append(name)
            except Exception as exc:
                errors.append(exc)

        background = threading.Thread(target=worker, args=("background", PRIORITY_BACKGROUND))
        web = threading.Thread(target=worker, args=("web", PRIORITY_WEB))
        try:
            background.start()
            self._wait_for_depth(1)
            web.start()
            self._wait_for_depth(2)
        finally:
            self.scheduler.release(holder)
            background.join(timeout=3)
            web.join(timeout=3)
        self.assertFalse(background.is_alive())
        self.assertFalse(web.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(order, ["web", "background"])

    def test_cancelled_background_request_never_runs(self):
        holder = self.scheduler.acquire(source="holder", priority=PRIORITY_WEB)
        cancel = threading.Event()
        outcome = []

        def worker():
            try:
                with self.scheduler.slot(
                    source="background", priority=PRIORITY_BACKGROUND,
                    cancel_event=cancel, timeout=2,
                ):
                    outcome.append("executed")
            except InterruptedError:
                outcome.append("cancelled")

        thread = threading.Thread(target=worker)
        try:
            thread.start()
            self._wait_for_depth(1)
            cancel.set()
            thread.join(timeout=2)
        finally:
            self.scheduler.release(holder)
            thread.join(timeout=2)
        self.assertEqual(outcome, ["cancelled"])
        self.assertEqual(self.scheduler.status()["queue_depth"], 0)

    def test_wait_timeout_does_not_leak_queue_entry(self):
        holder = self.scheduler.acquire(source="holder", priority=PRIORITY_WEB)
        try:
            with self.assertRaises(TimeoutError):
                self.scheduler.acquire(source="background", priority=PRIORITY_BACKGROUND, timeout=0.01)
            self.assertEqual(self.scheduler.status()["queue_depth"], 0)
        finally:
            self.scheduler.release(holder)

    def test_equal_priority_keeps_fifo_order(self):
        holder = self.scheduler.acquire(source="holder", priority=PRIORITY_WEB)
        order = []
        errors = []

        def worker(name):
            try:
                with self.scheduler.slot(source=name, priority=PRIORITY_BACKGROUND, timeout=2):
                    order.append(name)
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=worker, args=("first",))
        second = threading.Thread(target=worker, args=("second",))
        try:
            first.start()
            self._wait_for_depth(1)
            second.start()
            self._wait_for_depth(2)
        finally:
            self.scheduler.release(holder)
            first.join(timeout=3)
            second.join(timeout=3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(order, ["first", "second"])

    def test_priority_for_owner_and_background(self):
        self.assertEqual(InferenceScheduler.priority_for("web:owner"), PRIORITY_WEB)
        self.assertEqual(InferenceScheduler.priority_for("telegram:123"), PRIORITY_TELEGRAM)
        self.assertEqual(
            InferenceScheduler.priority_for("web:owner", background=True),
            PRIORITY_BACKGROUND,
        )


if __name__ == "__main__":
    unittest.main()
