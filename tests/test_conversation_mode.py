"""Work vs casual vs customer-draft detection: zero employer data or cross-chat leak."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.app import LeshyApp
from leshy.config import Settings
from leshy.conversation_mode import (
    resolve_conversation_mode, mode_instruction, conversation_rows_for_mode,
)
from leshy.realtime_chat import RealtimeChatManager


class ConversationModeTests(unittest.TestCase):
    def test_customer_draft_requires_user_signal(self):
        for value in (
            "/support Добрый день! На VPS низкая скорость",
            "Ответь клиенту: почему сеть медленная?",
            "Сообщение клиента: у меня не работает SSH",
            "Клиент: требуется увеличение канала",
        ):
            with self.subTest(value=value):
                self.assertEqual(resolve_conversation_mode(value).kind, "support_draft")

    def test_work_can_be_technical_without_customer_roleplay(self):
        for value in (
            "Проверь ошибки Kafka на сервере",
            "Исправь скрипт сборки",
            "Проверь PostgreSQL на Linux",
            "Помоги разобраться с тикетом",
            "/work продолжаем диагностику",
        ):
            with self.subTest(value=value):
                self.assertEqual(resolve_conversation_mode(value).kind, "work")

    def test_ordinary_chat_is_never_a_customer_letter(self):
        for value in (
            "Привет, как дела?",
            "Давай просто поговорим",
            "Расскажи анекдот",
            "Как работает SSH?",
            "Что думаешь о музыке?",
            "Как прошел твой день?",
        ):
            with self.subTest(value=value):
                self.assertEqual(resolve_conversation_mode(value).kind, "personal")

    def test_pasted_customer_quotation_is_a_draft(self):
        self.assertEqual(
            resolve_conversation_mode(
                "Добрый день! У вас на VPS ограничена скорость? Можно уточнить тариф?"
            ).kind, "support_draft"
        )

    def test_work_status_is_not_a_personal_greeting(self):
        self.assertEqual(resolve_conversation_mode("Как дела с Kafka?").kind, "work")
        self.assertEqual(resolve_conversation_mode("Что по серверу?").kind, "work")
        self.assertEqual(resolve_conversation_mode("Как дела?").kind, "personal")

    def test_private_reference_in_work_turn_only(self):
        import json
        import os
        from unittest.mock import patch
        from cryptography.fernet import Fernet
        from leshy.provider_support import work_reference_context
        with tempfile.TemporaryDirectory() as base:
            key = Fernet.generate_key()
            folder = Path(base) / "private"
            folder.mkdir()
            data = {"facts": [{"cues": ["vps"], "text": "Сверьте условия услуги."}]}
            (folder / "support_profile.enc").write_bytes(
                Fernet(key).encrypt(json.dumps(data).encode("utf-8"))
            )
            with patch.dict(os.environ, {"LESHY_SUPPORT_PROFILE_KEY": key.decode()}):
                self.assertIn(
                    "Сверьте условия услуги.",
                    work_reference_context("Проверь VPS", base),
                )
                self.assertEqual(work_reference_context("Как дела?", base), "")

    def test_followup_edits_ticket_without_manual_prefix(self):
        history = [
            {"role": "assistant", "content": "Уточните время и IP."},
            {"role": "user", "content": "Клиент: не работает SSH"},
        ]
        self.assertEqual(
            resolve_conversation_mode("Сделай короче", history).kind, "support_draft"
        )

    def test_normal_chat_interrupts_support_chain(self):
        history = [
            {"role": "assistant", "content": "Договор надо уточнить."},
            {"role": "user", "content": "Давай просто поговорим"},
            {"role": "assistant", "content": "Уточните время и IP."},
            {"role": "user", "content": "/support Клиент пишет: не работает SSH"},
        ]
        self.assertEqual(resolve_conversation_mode("Убери приветствие", history).kind, "personal")

    def test_explicit_personal_beats_old_ticket(self):
        history = [
            {"role": "user", "content": "/support Клиент: нужна помощь с VPS"},
        ]
        self.assertEqual(resolve_conversation_mode("/chat А как дела?", history).kind, "personal")
        self.assertEqual(resolve_conversation_mode("Не по работе. Как ты?", history).kind, "personal")

    def test_work_followup_stays_work_but_greeting_does_not(self):
        history = [
            {"role": "assistant", "content": "Проверь журналы."},
            {"role": "user", "content": "Проверь ошибки Kafka на сервере"},
        ]
        self.assertEqual(resolve_conversation_mode("И что дальше?", history).kind, "work")
        self.assertEqual(resolve_conversation_mode("Как дела?", history).kind, "personal")

    def test_personal_history_does_not_include_prior_customer_ticket(self):
        rows = [
            {"role": "user", "content": "Давай просто поговорим"},
            {"role": "assistant", "content": "Мы проверим Вашу услугу."},
            {"role": "user", "content": "/support Клиент: не работает SSH"},
        ]
        only_personal = conversation_rows_for_mode(rows, "personal")
        self.assertEqual(len(only_personal), 1)
        self.assertIn("просто поговорим", only_personal[0]["content"])

    def test_several_work_followups_keep_work_mode(self):
        rows = [
            {"role": "user", "content": "А теперь?"},
            {"role": "assistant", "content": "Следующий шаг"},
            {"role": "user", "content": "И что дальше?"},
            {"role": "assistant", "content": "Проверить логи"},
            {"role": "user", "content": "Проверь ошибки Kafka на сервере"},
        ]
        self.assertEqual(resolve_conversation_mode("А теперь?", rows).kind, "work")
        self.assertEqual(len(conversation_rows_for_mode(rows, "work")), 5)

    def test_user_sessions_are_isolated_in_routing(self):
        # Mode depends entirely on supplied same-session rows.
        support_history = [{"role": "user", "content": "/support Клиент: нет SSH"}]
        private_history = [{"role": "user", "content": "Просто поговорим"}]
        self.assertEqual(resolve_conversation_mode("Сделай короче", support_history).kind, "support_draft")
        self.assertEqual(resolve_conversation_mode("Сделай короче", private_history).kind, "personal")

    def test_instruction_is_distinct(self):
        work = mode_instruction("work")
        personal = mode_instruction("personal")
        self.assertIn("технической задачей", work)
        self.assertNotIn("техподдержки", work.split("ТЕКУЩИЙ КОНТЕКСТ:", 1)[0])
        self.assertIn("обычная беседа", personal)
        self.assertNotEqual(work, personal)

    def settings(self, root: Path) -> Settings:
        brain = root / "LeshyBrain"
        return Settings(
            install_root=root, brain_dir=brain, data_dir=brain / "data",
            database_path=brain / "data" / "leshy.sqlite3",
            native_models_dir=brain / "models", workspace_root=brain / "workspace",
            llm_backend="echo", embeddings_enabled=False,
        )

    def test_chat_mode_does_not_rewrite_casual_conversation_as_customer_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LeshyApp(self.settings(Path(tmp)))
            answer = app.chat("Привет, как дела?", owner_id="web:test")
            self.assertEqual(answer["answer"], "Эхо: Привет, как дела?")

    def test_stream_uses_ticket_mode_then_returns_to_personal(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LeshyApp(self.settings(Path(tmp)))
            live = RealtimeChatManager(app)
            ticket = list(live.stream(
                "Клиент: на VPS скорость 40 Мбит/с, в тарифе 200 Мбит/с",
                owner_id="web:mine",
            ))
            self.assertIn("200 Мбит/с", ticket[-1].data["answer"])
            sid = ticket[-1].data["session_id"]
            personal = list(live.stream(
                "Давай просто поговорим", owner_id="web:mine", session_id=sid,
            ))
            self.assertNotIn("200 Мбит/с", personal[-1].data.get("answer") or "")
            self.assertNotIn("необходимо", personal[-1].data.get("answer") or "")


if __name__ == "__main__":
    unittest.main()
