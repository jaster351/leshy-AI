from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from leshy.owner_knowledge_capture import (
    is_owner_document_save_request, is_owner_document_status_request,
    save_owner_document, owner_document_status, owner_document_context,
)
from leshy.parcel_tracking import parse_parcel_intent, extract_tracking_numbers
from leshy.dialogue_rules import is_rule_inspection_request
from leshy.storage import Storage, utcnow


class _App:
    def __init__(self, root):
        self.storage = Storage(root / "db.sqlite3")


class OwnerKnowledgeRoutingTests(unittest.TestCase):
    def test_processors_are_not_parcels(self):
        for text in ("I7-9700K", "i9-13900K", "i5-12400F", "I7-9700K: нет в наличии"):
            self.assertIsNone(parse_parcel_intent(text))
        self.assertEqual(extract_tracking_numbers("I7-9700K"), [])
        self.assertEqual(parse_parcel_intent("RR123456789CN").action, "track")
        self.assertEqual(parse_parcel_intent("Следи за RR123456789CN").action, "follow")

    def test_write_intent_is_not_inspection(self):
        command = ("Леший, текущая задача — сохранить ранее переданную инструкцию "
                   "технической поддержки Cloud4box в Knowledge.")
        self.assertTrue(is_owner_document_save_request(command))
        self.assertFalse(is_rule_inspection_request(command))
        self.assertFalse(is_owner_document_save_request("Какие правила ты сохранил?"))
        self.assertFalse(is_owner_document_save_request("Не сохраняй инструкцию Cloud4box"))
        status = "Проверь, сохранились ли постоянные правила Cloud4box в Knowledge?"
        self.assertTrue(is_owner_document_status_request(status))
        self.assertFalse(is_rule_inspection_request(status))

    def test_full_document_read_back_and_owner_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            app = _App(Path(temp))
            source = (
                "# ЛЕШИЙ — ИНСТРУКЦИЯ CLOUD4BOX\n"
                "## 1. Назначение\n" + "Обработка клиентских обращений. " * 50 +
                "\n## 2. Стиль\nНе здоровайся с клиентами.\n" +
                "## 3. VPN\nНе предлагай VPN-клиент на сервере.\n" +
                "## 4. Сохранение\nСохрани настоящую инструкцию в Knowledge.\n"
            )
            now = utcnow()
            with app.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("one", "telegram:101", "Техподдержка", now, now),
                )
                conn.execute("INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                             ("one", "user", source, now))
            command = "Леший, сохрани ранее переданную инструкцию Cloud4box в Knowledge."
            answer = save_owner_document(app, "telegram:101", "one", command)
            self.assertIn("Чтение и контрольная сумма проверены", answer)
            self.assertIn("Чтение выполнено", owner_document_status(app.storage, "telegram:101"))
            self.assertIn("Не здоровайся", owner_document_context(
                app.storage, "telegram:101", "Напиши ответ клиенту", support=True))
            self.assertEqual(owner_document_context(
                app.storage, "telegram:202", "Напиши ответ клиенту", support=True), "")
            repeat = save_owner_document(app, "telegram:101", "one", command)
            self.assertIn("Чтение и контрольная сумма проверены", repeat)
            with app.storage.connection() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM memories WHERE owner_id=? AND kind=?",
                    ("telegram:101", "owner_knowledge_document"),
                ).fetchone()["n"]
            self.assertEqual(n, 1)

    def test_missing_source_does_not_claim_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            app = _App(Path(temp))
            now = utcnow()
            with app.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("one", "telegram:101", "Техподдержка", now, now),
                )
            answer = save_owner_document(
                app, "telegram:101", "one", "Сохрани инструкцию Cloud4box в Knowledge")
            self.assertIn("не сохранена", answer)


if __name__ == "__main__":
    unittest.main()
