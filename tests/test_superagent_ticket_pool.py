"""Parallel ticket dispatch never double-books or releases a live worker early."""
from __future__ import annotations

import threading
import time
import unittest

from leshy.superagent_ticket_pool import ParallelTicketPool


class TicketPoolTests(unittest.TestCase):
    def test_independent_tickets_run_concurrently_up_to_bound(self):
        jobs = [{"id": f"T{i}"} for i in range(7)]
        active = set()
        maximum = 0
        done = []
        lock = threading.Lock()
        def next_due(exclude):
            return next((job for job in jobs if job["id"] not in exclude and job["id"] not in done), None)
        def worker(ticket):
            nonlocal maximum
            with lock:
                active.add(ticket["id"])
                maximum = max(maximum, len(active))
            time.sleep(0.015)
            with lock:
                active.remove(ticket["id"])
                done.append(ticket["id"])
        with ParallelTicketPool(3) as pool:
            while len(done) < len(jobs):
                pool.reap()
                pool.fill(next_due, worker, stopped=lambda: False)
                time.sleep(0.002)
            pool.reap()
            self.assertEqual(pool.active_ids, frozenset())
        self.assertEqual(maximum, 3)
        self.assertEqual(len(done), len(set(done)))
        self.assertEqual(len(done), 7)

    def test_error_does_not_prevent_other_tickets(self):
        completed = []
        with ParallelTicketPool(2) as pool:
            tasks = [{"id": "bad"}, {"id": "good"}]
            def next_due(exclude):
                return next((t for t in tasks if t["id"] not in exclude), None)
            def execute(ticket):
                if ticket["id"] == "bad":
                    raise RuntimeError("test error")
                completed.append(ticket["id"])
            self.assertEqual(pool.fill(next_due, execute, stopped=lambda: False), 2)
            while len(completed) < 1 or len(pool.active_ids):
                errors = pool.reap()
                if errors:
                    self.assertEqual(errors[0][0], "bad")
                time.sleep(0.001)
        self.assertEqual(completed, ["good"])

    def test_duplicate_ticket_refused_without_double_run(self):
        started = threading.Event()
        release = threading.Event()
        def worker(ticket):
            started.set()
            release.wait(timeout=2)
        with ParallelTicketPool(2) as pool:
            with self.assertRaisesRegex(ValueError, "уже назначенный"):
                pool.fill(lambda excluded: {"id": "same"}, worker, stopped=lambda: False)
            self.assertTrue(started.wait(timeout=1))
            self.assertEqual(pool.active_ids, frozenset({"same"}))
            release.set()

    def test_stop_prevents_submitting_jobs(self):
        with ParallelTicketPool(8) as pool:
            scheduled = pool.fill(lambda excluded: {"id": "ticket"},
                                  lambda ticket: self.fail("should not execute"),
                                  stopped=lambda: True)
            self.assertEqual(scheduled, 0)

    def test_worker_count_scales_beyond_eight_for_healthy_nodes(self):
        with ParallelTicketPool(48) as pool:
            self.assertEqual(pool.max_workers, 48)
        with self.assertRaises(ValueError):
            ParallelTicketPool(129)

    def test_backpressure_admits_no_new_work_under_memory_pressure(self):
        from concurrent.futures import Future
        started = threading.Event()
        release = threading.Event()
        submitted = []
        def worker(ticket):
            submitted.append(ticket["id"])
            started.set()
            release.wait(timeout=2)
        tasks = [{"id": str(i)} for i in range(3)]
        with ParallelTicketPool(16) as pool:
            self.assertEqual(pool.fill(lambda exclude: tasks[0] if "0" not in exclude else None,
                                       worker, stopped=lambda: False, capacity=1), 1)
            self.assertTrue(started.wait(timeout=1))
            self.assertEqual(pool.fill(lambda exclude: tasks[1], worker, stopped=lambda: False,
                                       capacity=0), 0)
            self.assertEqual(submitted, ["0"])
            release.set()

    def test_memory_limit_cannot_be_bypassed_with_bad_capacity(self):
        with ParallelTicketPool(16) as pool:
            for value in (-1, True, 1.0):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        pool.fill(lambda excluded: None, lambda ticket: None,
                                  stopped=lambda: False, capacity=value)


if __name__ == "__main__":
    unittest.main()
