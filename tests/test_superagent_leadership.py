"""Лидерство SuperAgent для одной SQLite базы: не допускать двойного execution."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from leshy.cognitive_lease import SuperAgentProcessLease
from leshy.storage import Storage
from leshy.superagent import SuperAgentService


class SuperAgentLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "leshy.sqlite3"
        self.storage = Storage(self.db)
        self.app = SimpleNamespace(storage=self.storage, knowledge=Mock(), llm=Mock())

    def _make(self):
        service = SuperAgentService(self.app)
        self.addCleanup(service.stop)
        return service

    def test_file_lock_exclusive_and_reacquirable(self):
        first = SuperAgentProcessLease(self.db)
        second = SuperAgentProcessLease(self.db)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertTrue(first.try_acquire())
        self.assertTrue(first.try_acquire())
        self.assertFalse(second.try_acquire())
        first.release()
        self.assertTrue(second.try_acquire())

    def test_follower_does_not_reset_active_working_ticket(self):
        leader = self._make()
        self.assertTrue(leader._lease.held)
        ticket = leader.create("web:owner", "", "Объясни, зачем нужен журнал")
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='working' WHERE id=?", (ticket["id"],))
        follower = self._make()
        self.assertFalse(follower._lease.held)
        self.assertEqual(follower.get(ticket["id"], owner_id="web:owner")["state"], "working")
        leader.stop()
        self.assertTrue(follower._activate())
        self.assertEqual(follower.get(ticket["id"], owner_id="web:owner")["state"], "queued")

    def test_only_leader_can_approve_proposal(self):
        leader = self._make()
        ticket = leader.create("web:owner", "", "Создай файл с кратким отчётом")
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='working' WHERE id=?", (ticket["id"],))
        proposal = leader._artifacts.propose(ticket["id"], "web:owner", "Отчёт", "Содержимое")
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='waiting' WHERE id=?", (ticket["id"],))
        follower = self._make()
        with self.assertRaises(RuntimeError):
            follower.approve_artifact(proposal["id"], "web:owner")
        self.assertFalse((Path(self.temp.name) / "workspace").exists())
        self.assertEqual(leader.approve_artifact(proposal["id"], "web:owner")["state"], "applied")

    def test_restart_recovery_happens_only_after_leadership(self):
        leader = self._make()
        ticket = leader.create("web:owner", "", "Объясни тему")
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='working' WHERE id=?", (ticket["id"],))
        follower = self._make()
        self.assertFalse(follower._lease.held)
        leader.stop()
        self.assertTrue(follower._activate())
        self.assertEqual(follower.get(ticket["id"], owner_id="web:owner")["state"], "queued")

    def test_lease_release_does_not_delete_lock_file(self):
        first = SuperAgentProcessLease(self.db)
        self.assertTrue(first.try_acquire())
        first.release()
        self.assertTrue(first.path.exists())
        second = SuperAgentProcessLease(self.db)
        self.addCleanup(second.release)
        self.assertTrue(second.try_acquire())


if __name__ == "__main__":
    unittest.main()
