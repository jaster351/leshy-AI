"""Crash-recovery budget must prevent an infinite retry loop."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from leshy.storage import Storage
from leshy.superagent import SuperAgentService


class SuperAgentRecoveryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"LESHY_SUPERAGENT_MAX_ATTEMPTS": "3"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Storage(Path(self.temp.name) / "brain.sqlite3")
        self.app = SimpleNamespace(storage=self.storage, knowledge=Mock(), llm=Mock())

    def service(self):
        worker = SuperAgentService(self.app)
        worker.max_attempts = 3
        self.addCleanup(worker.stop)
        return worker

    def mark_working(self, ticket_id, attempt):
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE superagent_tickets SET state='working',attempt=? WHERE id=?",
                (attempt, ticket_id),
            )
            conn.execute(
                "INSERT INTO superagent_assignments(id,ticket_id,attempt,state,started_at) "
                "VALUES(?,?,?,?,?)",
                (f"{ticket_id}-A{attempt}", ticket_id, attempt,
                 "running", "2026-11-12T00:00:00+00:00"),
            )

    def test_recovery_requeues_job_below_budget_and_closes_assignment(self):
        worker = self.service()
        ticket = worker.create("web:owner", "", "Собери обзор")
        self.mark_working(ticket["id"], 2)
        worker.stop()
        recovered = self.service()
        item = recovered.get(ticket["id"], owner_id="web:owner")
        self.assertEqual(item["state"], "queued")
        self.assertIn("прерван", item["latest_report"])
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT state,outcome FROM superagent_assignments WHERE ticket_id=?",
                (ticket["id"],),
            ).fetchone()
        self.assertEqual((row["state"], row["outcome"]), ("ended", "interrupted"))

    def test_recovery_marks_exhausted_ticket_failed_without_new_assignment(self):
        worker = self.service()
        ticket = worker.create("web:owner", "", "Собери обзор")
        self.mark_working(ticket["id"], 3)
        worker.stop()
        recovered = self.service()
        item = recovered.get(ticket["id"], owner_id="web:owner")
        self.assertEqual(item["state"], "failed")
        self.assertEqual(item["attempt"], 3)
        self.assertIn("Лимит попыток", item["latest_report"])
        self.assertIsNone(recovered._next_due())

    def test_other_owners_cannot_see_recovered_ticket(self):
        worker = self.service()
        ticket = worker.create("web:owner", "", "Обработай")
        self.mark_working(ticket["id"], 3)
        worker.stop()
        recovered = self.service()
        with self.assertRaises(PermissionError):
            recovered.get(ticket["id"], owner_id="web:other")


if __name__ == "__main__":
    unittest.main()
