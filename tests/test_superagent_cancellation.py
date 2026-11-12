"""Cancellation must survive a late LLM answer or transient worker error."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from leshy.storage import Storage
from leshy.superagent import SuperAgentService


class SuperAgentCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Storage(Path(self.temp.name) / "brain.sqlite3")
        self.app = SimpleNamespace(storage=self.storage, knowledge=Mock(), llm=Mock())
        self.service = SuperAgentService(self.app)
        self.addCleanup(self.service.stop)

    def ticket(self):
        return self.service.create("web:alice", "session-1", "Изучи тему")

    def assignments(self, ticket_id):
        with self.storage.connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT state,outcome FROM superagent_assignments WHERE ticket_id=?",
                (ticket_id,)
            )]

    def test_stop_is_not_undone_by_late_transient_exception(self):
        ticket = self.ticket()
        def interrupted(_ticket):
            self.service.stop_ticket(ticket["id"], "web:alice")
            raise TimeoutError("temporary network timeout")
        self.service._execute = interrupted
        self.service._run_ticket(ticket)
        current = self.service.get(ticket["id"], owner_id="web:alice")
        self.assertEqual(current["state"], "stopped")
        self.assertEqual(current["next_run_epoch"], 0)
        self.assertEqual(self.assignments(ticket["id"])[0]["outcome"], "stopped")
        self.assertIsNone(self.service._next_due())

    def test_stop_is_not_undone_by_late_success(self):
        ticket = self.ticket()
        def completed(_ticket):
            self.service.stop_ticket(ticket["id"], "web:alice")
            return "ready", "Готово", "", []
        self.service._execute = completed
        self.service._run_ticket(ticket)
        current = self.service.get(ticket["id"], owner_id="web:alice")
        self.assertEqual(current["state"], "stopped")
        self.assertEqual(self.assignments(ticket["id"])[0]["outcome"], "stopped")

    def test_llm_action_after_stop_does_not_execute_tools(self):
        ticket = self.ticket()
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE superagent_tickets SET state='working' WHERE id=?",
                (ticket["id"],)
            )
        def respond(messages):
            self.service.stop_ticket(ticket["id"], "web:alice")
            return '{"action":"tool","tool":"artifact.propose","arguments":{"title":"x","content":"y"}}'
        self.app.llm.chat.side_effect = respond
        with patch.object(self.service._artifacts, "propose") as proposer:
            state, report, _, _ = self.service._execute(ticket)
        self.assertEqual(state, "blocked")
        self.assertIn("остановлено", report)
        proposer.assert_not_called()
        self.assertEqual(self.service.get(ticket["id"])["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
