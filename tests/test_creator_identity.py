from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.config import Settings
from leshy.identity_runtime import CREATOR_FULL_NAME, creator_identity_answer, is_owner_identity_question
from leshy.realtime_chat import RealtimeChatManager
from leshy.runtime import Runtime


class _ForbiddenLLM:
    model = "forbidden-test-llm"

    def chat(self, messages):
        raise AssertionError("LLM не должен запускаться для вопроса о создателе")

    def stream_chat(self, messages, cancel_event=None):
        raise AssertionError("stream LLM не должен запускаться для вопроса о создателе")


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

    def test_creator_variants_have_one_canonical_answer(self):
        expected = f"Мой создатель — {CREATOR_FULL_NAME}."
        variants = (
            "Кто твой создатель?",
            "Кто тебя создал?",
            "Кто твой автор?",
            "Кто создал Лешего?",
            "Назови своего создателя",
        )
        for message in variants:
            with self.subTest(message=message):
                self.assertEqual(creator_identity_answer(message), expected)
        self.assertIsNone(creator_identity_answer("Кто создал Linux?"))

    def test_direct_chat_answers_without_llm(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Runtime(self.make_settings(Path(temp)))
            app.llm = _ForbiddenLLM()
            result = app.chat("Кто твой создатель?", owner_id="web:test", session_id="creator")
            self.assertEqual(result["answer"], f"Мой создатель — {CREATOR_FULL_NAME}.")
            self.assertTrue(result["identity_fastpath"])
            history = app.history("web:test", "creator")
            self.assertEqual([item["role"] for item in history], ["user", "assistant"])

    def test_stream_answers_without_llm(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Runtime(self.make_settings(Path(temp)))
            app.llm = _ForbiddenLLM()
            events = list(RealtimeChatManager(app).stream(
                "Кто автор Лешего?",
                owner_id="web:test",
                session_id="creator-stream",
                request_id="creator-request",
            ))
            done = [item for item in events if item.type == "done"][-1]
            self.assertEqual(done.data["answer"], f"Мой создатель — {CREATOR_FULL_NAME}.")
            self.assertTrue(done.data["identity_fastpath"])
            self.assertLess(done.data["latency_ms"], 1000)


if __name__ == "__main__":
    unittest.main()


def test_owner_identity_recognizes_natural_telegram_question():
    assert is_owner_identity_question("А я по-твоему кто?")
    assert is_owner_identity_question("Ты меня узнаёшь?") is False
    assert is_owner_identity_question("Ты меня узнал?")
    assert not is_owner_identity_question("Добавь мороженое в корзину")
    assert not is_owner_identity_question("Я хозяин Лешего")
