"""Local regression tests for interactive SuperAgent task lifecycle, no GitHub CI required."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from leshy.superagent_dashboard import execute_action, list_tickets, ticket_details


class _Storage:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class _Service:
    def __init__(self, storage):
        self.storage = storage
        self.plans = SimpleNamespace(snapshot=lambda *args: None)
        with storage.connection() as conn:
            conn.execute(
                "CREATE TABLE superagent_tickets (id TEXT PRIMARY KEY, owner_id TEXT, "
                "title TEXT, state TEXT, latest_report TEXT, attention TEXT, "
                "attempt INTEGER, created_at TEXT, updated_at TEXT)"
            )
            conn.executemany("INSERT INTO superagent_tickets VALUES (?,?,?,?,?,?,?,?,?)", [
                ("SA-1", "web:alice", "Завершена", "ready", "Итоговый отчёт", "Старый вопрос", 1, "1", "1"),
                ("SA-2", "web:alice", "Нужен ответ", "waiting", "Промежуточный отчёт", "Ваш ответ?", 1, "1", "2"),
                ("SA-3", "web:bob", "Чужая задача", "working", "", "", 0, "1", "3"),
            ])

    def get(self, ticket_id, *, owner_id):
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT * FROM superagent_tickets WHERE id=? AND owner_id=?", (ticket_id, owner_id)
            ).fetchone()
        if row is None:
            raise PermissionError("Чужое задание")
        return dict(row)

    def _inputs(self, ticket_id):
        return [{"kind": "request", "content": "Проверь логи", "created_at": "1"},
                {"kind": "human", "content": "Да", "created_at": "2"}]

    def stop_ticket(self, ticket_id, owner_id):
        self.get(ticket_id, owner_id=owner_id)
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='stopped',attention='' WHERE id=?", (ticket_id,))
        return self.get(ticket_id, owner_id=owner_id)

    def answer(self, ticket_id, owner_id, value):
        self.get(ticket_id, owner_id=owner_id)
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_tickets SET state='queued',attention='' WHERE id=?", (ticket_id,))
        return self.get(ticket_id, owner_id=owner_id)


class DashboardArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = _Service(_Storage(os.path.join(self.directory.name, "tickets.sqlite")))

    def test_ready_not_waiting_and_archive_restore_preserves_report(self):
        active = list_tickets(self.service, "web:alice")["tasks"]
        self.assertEqual(len(active), 2)
        ready = next(row for row in active if row["id"] == "SA-1")
        self.assertEqual(ready["attention"], "")
        self.assertFalse(ready["can_answer"])
        archived = execute_action(self.service, "web:alice", {"action": "close", "id": "SA-1"})["task"]
        self.assertTrue(archived["archived"])
        self.assertEqual(len(list_tickets(self.service, "web:alice")["tasks"]), 1)
        self.assertEqual(len(list_tickets(self.service, "web:alice", archived=True)["tasks"]), 1)
        self.assertFalse(ticket_details(self.service, "web:alice", "SA-1")["task"]["can_close"])
        self.assertEqual(ticket_details(self.service, "web:alice", "SA-1")["task"]["latest_report"], "Итоговый отчёт")
        execute_action(self.service, "web:alice", {"action": "restore", "id": "SA-1"})
        self.assertEqual(len(list_tickets(self.service, "web:alice")["tasks"]), 2)

    def test_close_waiting_stops_work_before_archive(self):
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action": "archive", "id": "SA-2"})
        archived = execute_action(self.service, "web:alice", {"action": "close", "id": "SA-2"})["task"]
        self.assertEqual(archived["state"], "stopped")
        self.assertTrue(archived["archived"])
        self.assertFalse(archived["can_stop"])
        self.assertFalse(archived["can_retry"])

    def test_answer_and_owner_isolation(self):
        with self.assertRaises(PermissionError):
            execute_action(self.service, "web:bob", {"action": "close", "id": "SA-2"})
        self.assertEqual(list_tickets(self.service, "web:bob")["tasks"][0]["id"], "SA-3")
        answered = execute_action(
            self.service, "web:alice", {"action": "answer", "id": "SA-2", "text": "Да"}
        )["task"]
        self.assertEqual(answered["state"], "queued")
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action": "answer", "id": "SA-1", "text": "Да"})

    def test_details_owner_and_prior_user_messages(self):
        task = ticket_details(self.service, "web:alice", "SA-2")["task"]
        self.assertEqual([x["text"] for x in task["conversation"]], ["Проверь логи", "Да"])
        self.assertNotIn("owner_id", task)
        with self.assertRaises(PermissionError):
            ticket_details(self.service, "web:alice", "SA-3")


if __name__ == "__main__":
    unittest.main()
