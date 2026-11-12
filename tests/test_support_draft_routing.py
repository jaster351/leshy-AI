"""Employee-facing support drafts must not inherit assistant self-introduction."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from leshy.app import LeshyApp
from leshy.config import Settings
from leshy.llm import ChatMessage
from leshy.persona import with_leshy_identity
from leshy.provider_support import (
    SUPPORT_DRAFT_MODE, draft_support_reply, is_support_draft_request,
)
from leshy.realtime_chat import RealtimeChatManager


BAD_REPLY = """Добрый день! Я — **Леший**.

Скорость 40 Мбит/с вместо заявленных 200 Мбит/с на вашем VPS может быть вызвана причинами:

1. Сетевая карта или драйвер
2. Ограничение со стороны провайдера

**Что можно сделать:**
- Обратитесь в поддержку вашего провайдера VPS.
"""


class SupportDraftRoutingTests(unittest.TestCase):
    def settings(self, root: Path) -> Settings:
        brain = root / "LeshyBrain"
        return Settings(
            install_root=root, brain_dir=brain, data_dir=brain / "data",
            database_path=brain / "data" / "leshy.sqlite3",
            native_models_dir=brain / "models", workspace_root=brain / "workspace",
            llm_backend="echo", embeddings_enabled=False,
        )

    def test_200_vs_40_ticket_is_a_ready_customer_reply_without_llm(self):
        ticket = (
            "/support Клиент пишет: Добрый день! На VPS скорость 40 Мбит/с, "
            "хотя в тарифе указано 200 Мбит/с. Почему так?"
        )
        infer = Mock(side_effect=AssertionError("Should use verified generic response"))
        answer = draft_support_reply(infer, ticket)
        infer.assert_not_called()
        self.assertIn("200 Мбит/с", answer)
        self.assertIn("40", ticket)
        self.assertNotIn("Леший", answer)
        self.assertNotIn("свяжитесь", answer.lower())
        self.assertNotIn("1.", answer)

    def test_invalid_ai_answer_is_never_shown_as_customer_message(self):
        infer = Mock(side_effect=[BAD_REPLY, BAD_REPLY])
        answer = draft_support_reply(
            infer, "/support Клиент жалуется на SSH, требуется ответ",
        )
        self.assertEqual(infer.call_count, 2)
        self.assertNotIn("Леший", answer)
        self.assertNotIn("Обратитесь в поддержку", answer)
        self.assertNotIn("1.", answer)

    def test_valid_draft_is_single_model_call(self):
        infer = Mock(return_value="Уточните, пожалуйста, ID услуги и время сбоя.")
        answer = draft_support_reply(
            infer, "Ответь клиенту: не работает SSH на сервере",
        )
        self.assertEqual(answer, "Уточните, пожалуйста, ID услуги и время сбоя.")
        self.assertEqual(infer.call_count, 1)
        sent = infer.call_args.args[0]
        self.assertTrue(sent[0].content.startswith(SUPPORT_DRAFT_MODE))
        self.assertIn("не работает SSH", sent[-1].content)

    def test_followup_edits_reuse_previous_support_ticket(self):
        infer = Mock(return_value="Уточните, пожалуйста, результаты замеров.")
        rows = [
            {"role": "user", "content": "Сделай короче"},
            {"role": "assistant", "content": "Ранее подготовленный ответ клиенту."},
            {"role": "user", "content": "/support Клиент: скорость снизилась"},
        ]
        self.assertTrue(is_support_draft_request("Сделай короче", rows))
        reply = draft_support_reply(infer, "Сделай короче", rows)
        self.assertIn("результаты", reply)
        sent = infer.call_args.args[0][-1].content
        self.assertIn("скорость снизилась", sent)
        self.assertIn("Сделай короче", sent)

    def test_support_mode_does_not_add_leshy_persona(self):
        prepared = with_leshy_identity([
            ChatMessage("system", SUPPORT_DRAFT_MODE + "\nТекст для клиента"),
            ChatMessage("user", "Письмо клиенту"),
        ])
        self.assertIn(SUPPORT_DRAFT_MODE, prepared[0].content)
        self.assertNotIn("Ты — Леший", prepared[0].content)

    def test_regular_persona_is_unchanged(self):
        prepared = with_leshy_identity([ChatMessage("user", "Привет")])
        self.assertIn("Ты — Леший", prepared[0].content)

    def test_direct_chat_returns_only_customer_draft(self):
        with tempfile.TemporaryDirectory() as base:
            app = LeshyApp(self.settings(Path(base)))
            ticket = "/support Клиент пишет: на VPS скорость 40 Мбит/с, в тарифе 200 Мбит/с"
            reply = app.chat(ticket, owner_id="web:one")
            self.assertIn("200 Мбит/с", reply["answer"])
            self.assertNotIn("Эхо:", reply["answer"])
            self.assertNotIn("Я Леший", reply["answer"])
            self.assertEqual(len(app.history("web:one", reply["session_id"])), 2)

    def test_streaming_support_waits_for_validated_final_answer(self):
        with tempfile.TemporaryDirectory() as base:
            app = LeshyApp(self.settings(Path(base)))
            stream = RealtimeChatManager(app)
            events = list(stream.stream(
                "/support Клиент: на VPS скорость 40 Мбит/с, в тарифе 200 Мбит/с",
                owner_id="web:one",
            ))
            self.assertEqual(events[0].type, "start")
            self.assertEqual(events[-1].type, "done")
            answer = events[-1].data["answer"]
            self.assertIn("200 Мбит/с", answer)
            self.assertNotIn("Эхо:", answer)
            self.assertEqual("".join(x.data["text"] for x in events if x.type == "delta"), answer)

    def test_profile_status_never_discloses_key(self):
        with tempfile.TemporaryDirectory() as base:
            from leshy.provider_support import support_profile_status
            reply = support_profile_status(base)
            self.assertIn("не подключён", reply)
            self.assertNotIn("support.key.dpapi", reply)


if __name__ == "__main__":
    unittest.main()
