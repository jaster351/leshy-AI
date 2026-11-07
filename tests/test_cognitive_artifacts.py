"""Этап 4 Cognitive Core: подтверждение владельца и создание файлов без перезаписи."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from leshy.storage import Storage
from leshy.superagent import SuperAgentService, parse_superagent_intent


class ArtifactApprovalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.storage = Storage(self.root / "leshy.sqlite3")
        app = SimpleNamespace(
            storage=self.storage,
            settings=SimpleNamespace(workspace_root=self.root / "workspace"),
            knowledge=Mock(), llm=Mock(),
        )
        self.service = SuperAgentService(app)

    def _proposal(self):
        ticket = self.service.create("web:owner-a", "", "Создай файл с кратким отчётом")
        ticket_id = ticket["id"]
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='working' WHERE id=?", (ticket_id,))
        p = self.service._artifacts.propose(ticket_id, "web:owner-a", "Отчёт", "# Готово")
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='waiting' WHERE id=?", (ticket_id,))
        return ticket_id, p

    def test_requires_approval_and_owner_isolation(self):
        _, p = self._proposal()
        self.assertFalse((self.root / "workspace").exists())
        with self.assertRaises(PermissionError):
            self.service.preview_artifact(p["id"], "web:owner-b")
        with self.assertRaises(PermissionError):
            self.service.approve_artifact(p["id"], "web:owner-b")
        self.assertFalse((self.root / "workspace").exists())

    def test_approve_creates_verified_once_only(self):
        ticket_id, p = self._proposal()
        self.assertEqual(self.service.preview_artifact(p["id"], "web:owner-a")["content"], "# Готово")
        done = self.service.approve_artifact(p["id"], "web:owner-a")
        output = Path(done["path"])
        self.assertEqual(output.read_text(encoding="utf-8"), "# Готово")
        self.assertEqual(self.service.get(ticket_id, owner_id="web:owner-a")["state"], "ready")
        self.assertEqual(self.service.approve_artifact(p["id"], "web:owner-a")["path"], str(output))

    def test_applied_artifact_must_still_exist_and_match_hash(self):
        _, proposal = self._proposal()
        done = self.service.approve_artifact(proposal["id"], "web:owner-a")
        file = Path(done["path"])
        file.write_text("changed by another process", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.approve_artifact(proposal["id"], "web:owner-a")

    def test_rejection_does_not_write(self):
        ticket_id, p = self._proposal()
        self.service.reject_artifact(p["id"], "web:owner-a")
        self.assertEqual(self.service.get(ticket_id, owner_id="web:owner-a")["state"], "blocked")
        with self.assertRaises(ValueError):
            self.service.approve_artifact(p["id"], "web:owner-a")
        self.assertFalse((self.root / "workspace").exists())

    def test_no_overwrite_and_failed_state_requires_review(self):
        _, p = self._proposal()
        row = self.service._artifacts._row(p["id"], "web:owner-a")
        target = self.service._artifacts._target(row)
        target.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.service.approve_artifact(p["id"], "web:owner-a")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        with self.assertRaises(ValueError):
            self.service.approve_artifact(p["id"], "web:owner-a")

    def test_cannot_propose_without_document_scope(self):
        ticket = self.service.create("web:owner-a", "", "Ответь, сколько времени")
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='working' WHERE id=?", (ticket["id"],))
        with self.assertRaises(PermissionError):
            self.service._artifacts.propose(ticket["id"], "web:owner-a", "x", "text")

    def test_commands_and_limited_scope(self):
        intent = parse_superagent_intent("/agent approve ART-" + "a" * 32)
        self.assertEqual(intent.action, "artifact_approve")
        self.assertIsNone(parse_superagent_intent("одобри ART-" + "a" * 32))


if __name__ == "__main__":
    unittest.main()
