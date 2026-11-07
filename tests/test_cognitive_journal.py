"""Регрессии этапа 3 Cognitive Core: запись шагов и fail-closed."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from leshy.cognitive_journal import CognitiveStepJournal
from leshy.storage import Storage
from leshy.superagent import SuperAgentService, format_ticket


class CognitiveStepJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Storage(Path(self.temp.name) / "test.sqlite3")
        self.service = SuperAgentService(
            SimpleNamespace(storage=self.storage, knowledge=Mock(), llm=Mock())
        )
        self.addCleanup(self.service.stop)
        self.ticket = self.service.create("web:owner-a", "session-a", "Объясни назначение версии")
        self.journal = self.service._journal

    def test_service_without_settings_keeps_safe_workspace_fallback(self):
        self.assertEqual(
            self.service._artifacts.workspace_root,
            Path(self.temp.name) / "workspace",
        )

    def test_log_stores_hashes_not_raw_arguments_or_responses(self):
        ticket_id = self.ticket["id"]
        self.journal.begin(ticket_id, 1, 1, "knowledge.search", {"query": "PRIVATE-QUERY"})
        self.journal.finish(ticket_id, 1, 1, status="success",
                            receipt_id="receipt-1", result={"claim": "PRIVATE-CONTENT"})
        steps = self.service.get(ticket_id, owner_id="web:owner-a")["steps"]
        self.assertEqual(steps[0]["status"], "success")
        self.assertNotIn("PRIVATE-QUERY", str(steps))
        self.assertNotIn("PRIVATE-CONTENT", str(steps))
        with self.storage.connection() as conn:
            row = dict(conn.execute(
                "SELECT * FROM superagent_steps WHERE ticket_id=?", (ticket_id,)
            ).fetchone())
        self.assertEqual(len(row["argument_sha256"]), 64)
        self.assertEqual(len(row["result_sha256"]), 64)
        self.assertNotIn("PRIVATE-QUERY", str(row))
        self.assertNotIn("PRIVATE-CONTENT", str(row))

    def test_duplicate_step_is_rejected_and_completion_requires_receipt(self):
        ticket_id = self.ticket["id"]
        self.journal.begin(ticket_id, 1, 1, "runtime.version", {})
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.begin(ticket_id, 1, 1, "runtime.version", {})
        with self.assertRaises(ValueError):
            self.journal.finish(ticket_id, 1, 1, status="success")
        self.journal.finish(ticket_id, 1, 1, status="failed")
        with self.assertRaises(ValueError):
            self.journal.finish(ticket_id, 1, 1, status="failed")

    def test_recovery_marks_started_step_interrupted(self):
        ticket_id = self.ticket["id"]
        self.journal.begin(ticket_id, 1, 1, "runtime.version", {})
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='working' WHERE id=?", (ticket_id,))
        self.service.stop()
        recovered = SuperAgentService(
            SimpleNamespace(storage=self.storage, knowledge=Mock(), llm=Mock())
        )
        self.addCleanup(recovered.stop)
        result = recovered.get(ticket_id, owner_id="web:owner-a")
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["steps"][0]["status"], "interrupted")

    def test_owner_cannot_read_other_tickets_steps(self):
        with self.assertRaises(PermissionError):
            self.service.get(self.ticket["id"], owner_id="web:owner-b")

    def test_status_includes_latest_step_without_contents(self):
        ticket_id = self.ticket["id"]
        self.journal.begin(ticket_id, 1, 1, "runtime.version", {})
        status = format_ticket(self.service.get(ticket_id, owner_id="web:owner-a"))
        self.assertIn("runtime.version", status)
        self.assertIn("started", status)


class CognitiveFailClosedReportTests(unittest.TestCase):
    def test_unstructured_output_is_not_ready(self):
        for raw in ("", "Готово, всё сделал", "[]", '{"status":"surprise","result":"готово"}',
                    '{"status":"ready","result":""}', '{"action":"tool","tool":"runtime.version"}'):
            with self.subTest(raw=raw):
                state, _, _ = SuperAgentService._parse_report(raw)
                self.assertEqual(state, "blocked")

    def test_valid_structured_report_remains_ready(self):
        state, report, _ = SuperAgentService._parse_report(
            '{"status":"ready","result":"Подготовлен отчёт","question":""}'
        )
        self.assertEqual(state, "ready")
        self.assertEqual(report, "Подготовлен отчёт")


if __name__ == "__main__":
    unittest.main()
