"""Проверка знаний модели о создателе и раздельной серверной авторизации."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.config import Settings
from leshy.context_budget import apply_context_budget
from leshy.identity_runtime import (
    CREATOR_FULL_NAME, creator_identity_answer, is_owner_identity_question,
    verified_telegram_identity_context, stream_with_verified_identity,
)
from leshy.llm import ChatMessage
from leshy.persona import LESHY_IDENTITY_PROMPT, with_leshy_identity
from leshy.realtime_chat import RealtimeChatManager
from leshy.research_policy import is_local_dialogue_request
from leshy.runtime import Runtime


class _CapturingLLM:
    model = "fixture-local-model"

    def __init__(self):
        self.requests = []

    def chat(self, messages):
        self.requests.append(with_leshy_identity(messages))
        return "Об авторе проекта я знаю из собственных базовых сведений."

    def stream_chat(self, messages, cancel_event=None):
        self.requests.append(with_leshy_identity(messages))
        yield "Мне известен создатель проекта; я отвечаю своими словами."


class CreatorIdentityTests(unittest.TestCase):
    def make_settings(self, root: Path) -> Settings:
        brain = root / "LeshyBrain"
        return Settings(
            install_root=root,
            brain_dir=brain,
            data_dir=brain / "data",
            database_path=brain / "data" / "leshy.sqlite3",
            native_models_dir=brain / "models",
            workspace_root=brain / "workspace",
            llm_backend="echo",
            embeddings_enabled=False,
        )

    def test_provenance_is_in_model_identity_not_a_scripted_reply(self):
        self.assertIn(CREATOR_FULL_NAME, LESHY_IDENTITY_PROMPT)
        self.assertIn("ПРОИСХОЖДЕНИЕ ЛЕШЕГО", LESHY_IDENTITY_PROMPT)
        for question in ("Кто твой создатель?", "Кто создал Лешего?",
                         "Назови своего автора"):
            with self.subTest(question=question):
                self.assertIsNone(creator_identity_answer(question))
                self.assertTrue(is_local_dialogue_request(question))

    def test_direct_chat_reaches_model_instead_of_fastpath(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Runtime(self.make_settings(Path(temp)))
            model = _CapturingLLM()
            app.llm = model
            result = app.chat("Кто твой создатель?", owner_id="web:test", session_id="creator")
            self.assertEqual(result["answer"],
                             "Об авторе проекта я знаю из собственных базовых сведений.")
            self.assertNotIn("identity_fastpath", result)
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(model.requests[0][0].role, "system")
            self.assertIn(CREATOR_FULL_NAME, model.requests[0][0].content)
            history = app.history("web:test", "creator")
            self.assertEqual([item["role"] for item in history], ["user", "assistant"])

    def test_streaming_chat_reaches_model_instead_of_fastpath(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Runtime(self.make_settings(Path(temp)))
            model = _CapturingLLM()
            app.llm = model
            events = list(RealtimeChatManager(app).stream(
                "Кто автор Лешего?", owner_id="web:test",
                session_id="creator-stream", request_id="creator-request",
            ))
            done = [item for item in events if item.type == "done"][-1]
            self.assertEqual(done.data["answer"],
                             "Мне известен создатель проекта; я отвечаю своими словами.")
            self.assertNotIn("identity_fastpath", done.data)
            self.assertTrue(model.requests)
            self.assertIn(CREATOR_FULL_NAME, model.requests[0][0].content)

    def test_creator_fact_survives_context_budget_trimming(self):
        source = with_leshy_identity([
            ChatMessage("system", "Справочный контекст. " * 3000),
            ChatMessage("user", "Кто создатель Лешего?"),
        ])
        result = apply_context_budget(source, context_size=4096, reserve_tokens=1024)
        self.assertIn("ПРОИСХОЖДЕНИЕ ЛЕШЕГО", result.messages[0].content)
        self.assertIn(CREATOR_FULL_NAME, result.messages[0].content)

    def test_verified_telegram_role_is_present_only_with_server_context(self):
        prompt = [ChatMessage("user", "А я по-твоему кто?")]
        without_role = with_leshy_identity(prompt)[0].content
        self.assertNotIn("ПОДТВЕРЖДЁННАЯ СЕРВЕРОМ РОЛЬ", without_role)
        with verified_telegram_identity_context("creator", server_owner=False):
            trusted = with_leshy_identity(prompt)[0].content
            self.assertIn("ПОДТВЕРЖДЁННАЯ СЕРВЕРОМ РОЛЬ", trusted)
            self.assertIn("создатель проекта", trusted)
            self.assertIn("Хозяин этой установки: нет", trusted)
        self.assertNotIn("ПОДТВЕРЖДЁННАЯ СЕРВЕРОМ РОЛЬ",
                         with_leshy_identity(prompt)[0].content)

    def test_telegram_stream_context_is_released_after_generation(self):
        def fake_stream():
            yield with_leshy_identity([ChatMessage("user", "кто я")])[0].content

        output = list(stream_with_verified_identity(
            fake_stream, role="server_owner", server_owner=True,
        ))
        self.assertEqual(len(output), 1)
        self.assertIn("хозяин этого сервера", output[0])
        self.assertNotIn("ПОДТВЕРЖДЁННАЯ СЕРВЕРОМ РОЛЬ",
                         with_leshy_identity([ChatMessage("user", "кто я")])[0].content)


if __name__ == "__main__":
    unittest.main()


def test_owner_identity_recognizes_natural_telegram_question():
    assert is_owner_identity_question("А я по-твоему кто?")
    assert is_owner_identity_question("Ты меня узнаёшь?")
    assert is_owner_identity_question("Ты меня узнал?")
    assert not is_owner_identity_question("Добавь мороженое в корзину")
    assert not is_owner_identity_question("Я хозяин Лешего")
