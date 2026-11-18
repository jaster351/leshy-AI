from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.app import LeshyApp
from leshy.config import Settings
from leshy.dialogue_rules import (
    is_direct_rule_request,
    is_rule_inspection_request,
    persistent_rule_context,
    save_direct_rule,
    verified_rule_report,
)
from leshy.realtime_chat import RealtimeChatManager
from leshy.storage import Storage

class _RecordingLLM:
    model = "recording-test-model"

    def __init__(self):
        self.last_messages = []

    def chat(self, messages):
        self.last_messages = list(messages)
        return "Ответ модели"

    def stream_chat(self, messages, cancel_event=None):
        self.last_messages = list(messages)
        yield "Ответ модели"

    def evidence(self):
        return "\n".join(m.content for m in self.last_messages if m.role == "system")



class DialogueRuleMemoryTests(unittest.TestCase):
    def test_explicit_rule_has_receipt_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "install"
            env = {
                "LESHY_INSTALL_ROOT": str(root),
                "LESHY_BRAIN_DIR": str(root / "LeshyBrain"),
                "LESHY_LLM_BACKEND": "echo",
            }
            with patch.dict(os.environ, env, clear=False):
                first = LeshyApp(Settings.from_env())
                first.llm = _RecordingLLM()
                result = first.chat(
                    "Установи постоянное правило: обращайся ко мне на Вы.",
                    owner_id="telegram:101",
                    session_id="telegram:101:101",
                )
                self.assertEqual(result["answer"], "Ответ модели")
                self.assertIn("Правило сохранено", first.llm.evidence())
                self.assertIn("ID записи:", first.llm.evidence())
                first.stop_background()

                second = LeshyApp(Settings.from_env())
                rule_context = second._memory_context("telegram:101")
                self.assertIn("обращайся ко мне на Вы", rule_context)
                self.assertNotIn("обращайся ко мне на Вы", second._memory_context("telegram:102"))
                self.assertTrue(any(
                    item["kind"] == "dialogue_rule"
                    for item in second.memories("telegram:101")
                ))
                second.stop_background()

    def test_web_stream_goes_through_verified_rule_save(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "LESHY_INSTALL_ROOT": str(Path(temp) / "install"),
                "LESHY_BRAIN_DIR": str(Path(temp) / "brain"),
                "LESHY_LLM_BACKEND": "echo",
            }
            with patch.dict(os.environ, env, clear=False):
                app = LeshyApp(Settings.from_env())
                app.llm = _RecordingLLM()
                manager = RealtimeChatManager(app)
                events = list(manager.stream(
                    "Категорически запрещаю использовать звёздочки в оформлении.",
                    owner_id="web:demo",
                ))
                self.assertEqual(events[-1].data.get("answer", ""), "Ответ модели")
                self.assertIn("Правило сохранено", app.llm.evidence())
                self.assertIn("звёздочки", app._memory_context("web:demo"))
                app.stop_background()

    def test_memory_question_reads_database_instead_of_model_guess(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "LESHY_INSTALL_ROOT": str(Path(temp) / "install"),
                "LESHY_BRAIN_DIR": str(Path(temp) / "brain"),
                "LESHY_LLM_BACKEND": "echo",
            }
            with patch.dict(os.environ, env, clear=False):
                app = LeshyApp(Settings.from_env())
                app.llm = _RecordingLLM()
                q = "Какие постоянные правила общения со мной ты помнишь?"
                self.assertTrue(is_rule_inspection_request(q))
                empty = app.chat(q, owner_id="telegram:123")
                self.assertEqual(empty["answer"], "Ответ модели")
                self.assertIn("нет подтверждённых", app.llm.evidence())
                app.chat("Запомни правило: отвечай коротко.", owner_id="telegram:123")
                self.assertIn("Правило сохранено", app.llm.evidence())
                report = app.chat(q, owner_id="telegram:123")
                self.assertEqual(report["answer"], "Ответ модели")
                self.assertIn("отвечай коротко", app.llm.evidence())
                self.assertIn("ID", app.llm.evidence())
                self.assertNotIn("отвечай коротко", verified_rule_report(app.storage, "telegram:456"))
                app.stop_background()

    def test_only_direct_instruction_is_captured(self):
        self.assertFalse(is_direct_rule_request(
            "Какие постоянные правила общения со мной ты помнишь?"
        ))
        self.assertFalse(is_direct_rule_request(
            "[Контекст Telegram Reply — приоритетный]\n"
            "Содержимое выбранного сообщения: Категорически запрещаю использовать звёздочки"
        ))
        self.assertFalse(is_direct_rule_request(
            "> Категорически запрещаю использовать звёздочки"
        ))
        self.assertFalse(is_direct_rule_request(
            "- Запомни правило: не используй звёздочки"
        ))
        self.assertTrue(is_direct_rule_request(
            "Категорически запрещаю использовать звёздочки"
        ))
        self.assertTrue(is_direct_rule_request(
            "Леший, запомни правило: отвечай на Вы"
        ))
        self.assertFalse(is_direct_rule_request(
            "Запомни, что завтра у меня встреча в 14:00"
        ))
        self.assertTrue(is_direct_rule_request(
            "Запомни следующие правила: не используй шаблонные вступления"
        ))

    def test_rejects_long_rule_and_secret_without_pretending_save(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(Path(temp) / "brain.db")
            long_rule = "Установи постоянное правило: " + ("слово " * 300)
            rule_id, receipt = save_direct_rule(storage, "telegram:101", long_rule)
            self.assertIsNone(rule_id)
            self.assertIn("не сохранено", receipt)
            secret_id, secret_receipt = save_direct_rule(
                storage, "telegram:101", "Запомни правило: password: supersecret"
            )
            self.assertIsNone(secret_id)
            self.assertIn("не сохранено", secret_receipt)
            self.assertEqual(persistent_rule_context(storage, "telegram:101"), "")

    def test_shared_legacy_owner_cannot_receive_private_style_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(Path(temp) / "brain.db")
            memory_id, receipt = save_direct_rule(
                storage, "web:local", "Установи постоянное правило: отвечай на Вы"
            )
            self.assertIsNone(memory_id)
            self.assertIn("не сохранено", receipt)
            self.assertEqual(persistent_rule_context(storage, "web:local"), "")

    def test_duplicate_rule_is_idempotent_and_owner_separated(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(Path(temp) / "brain.db")
            rule = "Сохрани правило: обращайся ко мне на Вы"
            first_id, _ = save_direct_rule(storage, "telegram:1", rule)
            second_id, _ = save_direct_rule(storage, "telegram:1", rule)
            other_id, _ = save_direct_rule(storage, "telegram:2", rule)
            self.assertIsNotNone(first_id)
            self.assertEqual(first_id, second_id)
            self.assertNotEqual(first_id, other_id)


if __name__ == "__main__":
    unittest.main()
