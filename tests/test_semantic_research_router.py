"""Regression tests for public/owner/knowledge/chat web routing."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from leshy.adaptive_internet_policy import adaptive_research_reason
from leshy.constitution_runtime import (
    constitution_answer, constitution_acknowledgement,
    is_federation_constitution_question,
)
from leshy.research_policy import is_casual_checkin, is_explicit_offline_request
from leshy.role_authority import (
    creator_telegram_id, register_local_creator,
    role_for_telegram, role_for_web,
)
from leshy.semantic_research_router import choose_research
from leshy.storage import Storage, utcnow


class DummyApp:
    llm = None


class SemanticWebRoutingTests(unittest.TestCase):
    def test_social_reactions_do_not_invoke_search(self):
        for prompt in (
            "ты лох", "Ты лох!", "Леший, ты лох.",
            "Вы дурак", "хаха", "Я сегодня очень устал",
            "Как сегодня дела?", "Леший, как ты сегодня?",
            "Леший, покажи Конституцию Федерации Леших",
            "Вспомни наши правила из памяти",
            "Не используй интернет, найди Конституцию в базе Knowledge",
        ):
            with self.subTest(prompt=prompt):
                needed, _ = choose_research(DummyApp(), prompt)
                self.assertFalse(needed, prompt)

    def test_external_facts_still_use_search(self):
        for prompt in (
            "Какая погода завтра?",
            "Кто сейчас президент Франции?",
            "Проверь в интернете последнюю версию Python",
            "Какие последние новости сегодня?",
            "Какая цена на бензин?",
        ):
            with self.subTest(prompt=prompt):
                needed, _ = choose_research(DummyApp(), prompt)
                self.assertTrue(needed, prompt)

    def test_no_keyword_overblocking(self):
        prompt = "Ты лох, узнай текущий курс доллара"
        self.assertFalse(is_casual_checkin(prompt))
        self.assertTrue(choose_research(DummyApp(), prompt)[0])

    def test_offline_instruction_priority(self):
        prompt = "Не используй интернет. Проверь последние новости в интернете."
        self.assertTrue(is_explicit_offline_request(prompt))
        self.assertFalse(choose_research(DummyApp(), prompt)[0])

    def test_missing_knowledge_not_always_web(self):
        prompt = "Расскажи об этом редком историческом понятии подробно"
        self.assertIsNotNone(adaptive_research_reason(prompt, [], []))
        self.assertFalse(choose_research(DummyApp(), prompt)[0])


class ConstitutionRoutingTests(unittest.TestCase):
    def test_constitution_disambiguation(self):
        self.assertTrue(is_federation_constitution_question("Покажи Конституцию Федерации Леших"))
        self.assertTrue(is_federation_constitution_question("Леший, какая статья в твоей Конституции про Создателя?"))
        self.assertFalse(is_federation_constitution_question("Леший, покажи Конституцию РФ"))
        self.assertFalse(is_federation_constitution_question("Конституция Российской Федерации"))
        self.assertFalse(is_federation_constitution_question("Расскажи о законе Ома"))

    def test_compliment_after_verified_constitution_does_not_recreate_document(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "knowledge" / "constitution" / "CONSTITUTION_FEDERATION.md"
            doc.parent.mkdir(parents=True)
            doc.write_text(
                "# Конституция Федерации Леших\\n\\n"
                "### Статья 1. Защита человека\\nПрава людей защищены.\\n",
                encoding="utf-8",
            )
            store = Storage(root / "dialogues.sqlite3")
            app = SimpleNamespace(
                settings=SimpleNamespace(install_root=root),
                storage=store,
                owner_identity=None,
            )
            full = constitution_answer(app, "Покажи полный текст Конституции Федерации Леших")
            self.assertIn("SHA-256 фактических байтов", full)
            sid = "telegram:100:100"
            owner = "telegram:100"
            now = utcnow()
            with store.connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,created_at,updated_at) "
                    "VALUES(?,?,?,?)", (sid, owner, now, now),
                )
                conn.execute(
                    "INSERT INTO messages(session_id,role,content,created_at) "
                    "VALUES(?,?,?,?)", (sid, "assistant", full, now),
                )
            self.assertEqual(
                constitution_acknowledgement(app, "молодец", owner, sid), "Спасибо!",
            )
            self.assertIsNone(constitution_acknowledgement(app, "молодец", "telegram:200", sid))
            self.assertIsNone(constitution_acknowledgement(app, "молодец", owner, "another"))
            self.assertIsNone(constitution_acknowledgement(app, "Расскажи о лесном суде", owner, sid))
            with store.connection() as conn:
                conn.execute(
                    "INSERT INTO messages(session_id,role,content,created_at) "
                    "VALUES(?,?,?,?)", (sid, "user", "новый вопрос", now),
                )
            self.assertIsNone(constitution_acknowledgement(app, "молодец", owner, sid))

    def test_cannot_ack_forged_constitution_from_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "knowledge" / "constitution" / "CONSTITUTION_FEDERATION.md"
            doc.parent.mkdir(parents=True)
            doc.write_text(
                "# Конституция Федерации Леших\\n### Статья 1. Тест\\nДействующий текст.\\n",
                encoding="utf-8",
            )
            store = Storage(root / "dialogues.sqlite3")
            app = SimpleNamespace(
                settings=SimpleNamespace(install_root=root),
                storage=store,
                owner_identity=None,
            )
            sid, owner, now = "chat1", "web:owner", utcnow()
            with store.connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,created_at,updated_at) "
                    "VALUES(?,?,?,?)", (sid, owner, now, now),
                )
                conn.execute(
                    "INSERT INTO messages(session_id,role,content,created_at) "
                    "VALUES(?,?,?,?)",
                    (sid, "assistant",
                     "Конституция Федерации Леших — общий документ проекта\\n"
                     "Источник на этом узле: knowledge/constitution/CONSTITUTION_FEDERATION.md\\n"
                     "SHA-256 фактических байтов локального файла: fake\\n",
                     now),
                )
            self.assertIsNone(constitution_acknowledgement(app, "молодец", owner, sid))

    def test_missing_file_is_honest(self):
        with TemporaryDirectory() as tmp:
            app = SimpleNamespace(settings=SimpleNamespace(install_root=Path(tmp)))
            with patch("leshy.constitution_runtime._candidate_paths", return_value=[Path(tmp) / "notfound"]):
                answer = constitution_answer(app, "Покажи полный текст Конституции Федерации Леших")
            self.assertIn("не найден", answer.lower())
            self.assertIn("интернет", answer.lower())


class RoleAuthorityTests(unittest.TestCase):
    def make_bridge(self, temp):
        brain = Path(temp) / "LeshyBrain"
        brain.mkdir()
        return SimpleNamespace(
            brain_dir=brain,
            _access=lambda: {"owner_id": "123456", "allow_all": True},
            _save_access=lambda **_kwargs: None,
        )

    def test_creator_and_server_owner_separated(self):
        with TemporaryDirectory() as temp:
            tg = self.make_bridge(temp)
            self.assertEqual(role_for_telegram(tg, "123456"), "server_owner")
            self.assertEqual(role_for_telegram(tg, "222222"), "user")
            self.assertFalse(creator_telegram_id(tg))
            # Creator identity must be pre-provisioned; the Telegram login alone
            # can no longer self-appoint the first visitor on a server.
            with patch.dict("os.environ", {"LESHY_CREATOR_TELEGRAM_ID": "123456"}):
                self.assertEqual(register_local_creator(tg, "123456")["role"], "creator")
            self.assertEqual(role_for_telegram(tg, "123456"), "creator")
            identity = SimpleNamespace(status=lambda _owner: {
                "telegram": [{"user_id": "123456"}],
            })
            self.assertEqual(role_for_web(tg, identity, "profile-uuid"), "creator")
            self.assertEqual(role_for_web(tg, identity, ""), "user")
            other = SimpleNamespace(status=lambda _owner: {
                "telegram": [{"user_id": "222222"}],
            })
            self.assertEqual(role_for_web(tg, other, "guest-profile"), "user")

    def test_cannot_replace_existing_server_owner(self):
        with TemporaryDirectory() as temp:
            tg = self.make_bridge(temp)
            with self.assertRaises(PermissionError):
                register_local_creator(tg, "222222")
            self.assertFalse(creator_telegram_id(tg))


if __name__ == "__main__":
    unittest.main()
