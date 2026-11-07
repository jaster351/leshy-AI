"""Проверка эффектов и обратимого отката документов Cognitive Core."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from leshy.storage import Storage
from leshy.superagent import SuperAgentService, parse_superagent_intent


class CognitiveRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = Storage(self.root / "db.sqlite3")
        app = SimpleNamespace(
            storage=self.storage, llm=Mock(), knowledge=Mock(),
            settings=SimpleNamespace(workspace_root=self.root / "workspace"),
        )
        self.service = SuperAgentService(app)
        self.addCleanup(self.service.stop)

    def make_applied(self):
        t = self.service.create("web:user", "", "Создай файл с отчётом")
        tid = t["id"]
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='working' WHERE id=?", (tid,))
        p = self.service._artifacts.propose(tid, "web:user", "Отчёт", "# Тест")
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='waiting' WHERE id=?", (tid,))
        applied = self.service.approve_artifact(p["id"], "web:user")
        return tid, p, Path(applied["path"])

    def test_verify_requires_matching_bytes(self):
        _, proposal, path = self.make_applied()
        self.assertTrue(self.service.verify_artifact(proposal["id"], "web:user")["verified"])
        path.write_text("changed", encoding="utf-8")
        status = self.service.verify_artifact(proposal["id"], "web:user")
        self.assertFalse(status["verified"])
        with self.assertRaises(ValueError):
            self.service.rollback_artifact(proposal["id"], "web:user")
        self.assertEqual(path.read_text(encoding="utf-8"), "changed")

    def test_explicit_rollback_archives_without_deleting_bytes(self):
        tid, proposal, path = self.make_applied()
        rolled = self.service.rollback_artifact(proposal["id"], "web:user")
        self.assertEqual(rolled["state"], "rolled_back")
        self.assertTrue(rolled["verified"])
        self.assertFalse(path.exists())
        archive = Path(rolled["path"])
        self.assertEqual(archive.read_text(encoding="utf-8"), "# Тест")
        self.assertEqual(self.service.get(tid, owner_id="web:user")["state"], "blocked")
        repeated = self.service.rollback_artifact(proposal["id"], "web:user")
        self.assertEqual(repeated["path"], rolled["path"])
        with self.assertRaises(ValueError):
            self.service.approve_artifact(proposal["id"], "web:user")

    def test_owner_cannot_verify_or_rollback_someone_else_file(self):
        _, proposal, path = self.make_applied()
        with self.assertRaises(PermissionError):
            self.service.verify_artifact(proposal["id"], "web:intruder")
        with self.assertRaises(PermissionError):
            self.service.rollback_artifact(proposal["id"], "web:intruder")
        self.assertTrue(path.exists())

    def test_crash_after_archiving_before_db_commit_recovers(self):
        tid, proposal, path = self.make_applied()
        row = self.service._artifacts._row(proposal["id"], "web:user")
        archive = self.service._rollback._archive_target(row, create_parent=True)
        path.rename(archive)
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO superagent_artifact_rollbacks(artifact_id,state,created_at) "
                "VALUES(?,'applying','test')", (proposal["id"],),
            )
        self.service.stop()
        new = SuperAgentService(self.service.app)
        self.addCleanup(new.stop)
        self.assertEqual(new.get(tid, owner_id="web:user")["state"], "blocked")
        result = new.verify_artifact(proposal["id"], "web:user")
        self.assertTrue(result["verified"])
        self.assertEqual(result["state"], "rolled_back")

    def test_reconcile_does_not_destroy_partial_rollback(self):
        tid, proposal, path = self.make_applied()
        row = self.service._artifacts._row(proposal["id"], "web:user")
        archive = self.service._rollback._archive_target(row, create_parent=True)
        import os
        os.link(path, archive)
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO superagent_artifact_rollbacks(artifact_id,state,created_at) "
                "VALUES(?,'applying','test')", (proposal["id"],),
            )
        self.service.stop()
        new = SuperAgentService(self.service.app)
        self.addCleanup(new.stop)
        self.assertTrue(path.exists())
        self.assertTrue(archive.exists())
        self.assertEqual(new._rollback._row(proposal["id"])["state"], "failed")
        self.assertEqual(new.get(tid, owner_id="web:user")["state"], "blocked")

    def test_artifact_task_cannot_retry_after_terminal_change(self):
        tid, proposal, _ = self.make_applied()
        self.service.rollback_artifact(proposal["id"], "web:user")
        with self.assertRaises(ValueError):
            self.service.retry(tid, "web:user")
        with self.assertRaises(ValueError):
            self.service.answer(tid, "web:user", "repeat")

    def test_explicit_commands(self):
        name = "ART-" + "a" * 32
        self.assertEqual(parse_superagent_intent("/agent verify " + name).action, "artifact_verify")
        self.assertEqual(parse_superagent_intent("/agent rollback " + name).action, "artifact_rollback")


if __name__ == "__main__":
    unittest.main()
