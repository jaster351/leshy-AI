"""Regression coverage for owner instruction capture, intent and answer control."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.action_intent_guard import (
    explicit_network_probe, local_document_study, local_memory_intent,
)
from leshy.browser_platform_runtime import parse_browser_conversation_intent
from leshy.conversation_mode import resolve_conversation_mode
from leshy.learning_commands import is_explicit_learning_command
from leshy.owner_knowledge_capture import (
    owner_document_context, owner_document_status, save_owner_document,
)
from leshy.response_integrity import is_correction_request, protect_corrected_answer
from leshy.storage import Storage, utcnow


class _App:
    def __init__(self, folder: Path):
        self.storage = Storage(folder / "leshy.db")


def _message(storage, sid: str, role: str, content: str) -> None:
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
            (sid, role, content, utcnow()),
        )


class TaskIntentIntegrityTests(unittest.TestCase):
    def test_local_memory_does_not_start_external_actions(self):
        prompt = (
            "Леший, сохрани ранее переданную инструкцию Cloud4box в Knowledge. "
            "В примерах упоминаются IP 1.1.1.1, SSH, оплата билетов и посылки."
        )
        self.assertTrue(local_memory_intent(prompt))
        self.assertFalse(explicit_network_probe(prompt, "1.1.1.1"))
        self.assertIsNone(parse_browser_conversation_intent(prompt))
        self.assertFalse(is_explicit_learning_command(prompt))
        self.assertTrue(local_document_study("Изучи инструкцию Cloud4box из истории"))
        self.assertFalse(is_explicit_learning_command("Изучи инструкцию Cloud4box из истории"))
        self.assertFalse(local_document_study("Изучи новейшие исследования по физике"))
        self.assertTrue(is_explicit_learning_command("Изучи новейшие исследования по физике"))

    def test_intent_still_allows_genuine_tools(self):
        self.assertTrue(explicit_network_probe("Проверь доступность IP 1.1.1.1", "1.1.1.1"))
        self.assertTrue(explicit_network_probe("1.1.1.1", "1.1.1.1"))
        self.assertFalse(explicit_network_probe(
            "Сообщение клиента: у нас проблемы SSH на 1.1.1.1", "1.1.1.1"))
        intent = parse_browser_conversation_intent("Посмотри цены билетов на самолёт")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "research")

    def test_rejected_answer_loop_stopped_without_model_retry(self):
        prior = "Я снова открыл браузер и нашёл статью о диспетчере устройств Windows."
        rows = [{"role": "assistant", "content": prior}]
        self.assertTrue(is_correction_request("Ты снова отвечаешь не на тот вопрос"))
        blocked, reason = protect_corrected_answer(
            "Ты снова отвечаешь не на тот вопрос", rows, prior)
        self.assertEqual(reason, "rejected_answer_repeated")
        self.assertIn("не считаю исходное задание выполненным", blocked)
        correct, reason = protect_corrected_answer(
            "Ты снова отвечаешь не на тот вопрос", rows,
            "Исправлено распознавание запроса; подтверждение выполнения ещё требуется.")
        self.assertEqual(reason, "")
        self.assertIn("Исправлено", correct)
        unchanged, reason = protect_corrected_answer("Повтори предыдущий ответ", rows, prior)
        self.assertEqual(reason, "")
        self.assertEqual(unchanged, prior)

    def test_work_followup_preserves_context_only_for_same_session_rows(self):
        context = [{"role": "assistant", "content": "Сохранил документ."},
                   {"role": "user", "content": "Сохрани инструкцию Cloud4box в Knowledge"}]
        self.assertEqual(resolve_conversation_mode(
            "Ты снова ответил не на тот вопрос", context).kind, "work")
        self.assertEqual(resolve_conversation_mode(
            "Давай просто поговорим", context).kind, "personal")

    def test_assistant_authored_document_saved_and_revised_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _App(Path(tmp))
            now = utcnow()
            with app.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("session-a", "telegram:11", "Cloud4box", now, now),
                )
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("session-b", "telegram:22", "Cloud4box", now, now),
                )
            document = (
                "# ЛЕШИЙ — CLOUD4BOX\n"
                "## 1. Правила\n" + "Никогда не здоровайся с клиентами. " * 30 +
                "\n## 2. Техническая поддержка\nОсновной IP при обычном обслуживании не меняется.\n"
                "## 3. Память\nПодтверждай запись чтением из LeshyBrain.\n"
            )
            _message(app.storage, "session-a", "user",
                     "Создай инструкцию технической поддержки Cloud4box")
            _message(app.storage, "session-a", "assistant", document)
            command = "Сохрани ранее переданную инструкцию Cloud4box в Knowledge"
            saved = save_owner_document(app, "telegram:11", "session-a", command)
            self.assertIn("Источник: assistant", saved)
            self.assertIn("Чтение и контрольная сумма проверены", saved)
            status = owner_document_status(app.storage, "telegram:11")
            self.assertIn("Контрольная сумма совпадает", status)
            self.assertIn("Никогда не здоровайся", owner_document_context(
                app.storage, "telegram:11", "Клиент спрашивает про IP", support=True))
            self.assertEqual(owner_document_context(
                app.storage, "telegram:22", "Клиент спрашивает про IP", support=True), "")
            self.assertIn("не сохранена", save_owner_document(
                app, "telegram:22", "session-b", command))
            revised = document.replace("не меняется", "остаётся неизменным")
            _message(app.storage, "session-a", "assistant", revised)
            changed = save_owner_document(app, "telegram:11", "session-a", command)
            self.assertIn("Чтение и контрольная сумма проверены", changed)
            with app.storage.connection() as conn:
                items = conn.execute(
                    "SELECT content,source FROM memories WHERE owner_id=? AND kind=?",
                    ("telegram:11", "owner_knowledge_document"),
                ).fetchall()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["content"], revised)
            self.assertIn("assistant", items[0]["source"])

    def test_unauthorized_session_cannot_supply_assistant_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _App(Path(tmp))
            now = utcnow()
            with app.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("only", "telegram:11", "Docs", now, now),
                )
            fake = "# Cloud4box\n## A\n" + ("Запомни. " * 75) + "\n## B\nВсе правила\n## C\n"
            _message(app.storage, "only", "assistant", fake)
            answer = save_owner_document(
                app, "telegram:22", "only", "Сохрани инструкцию Cloud4box")
            self.assertIn("не сохранена", answer)


if __name__ == "__main__":
    unittest.main()
