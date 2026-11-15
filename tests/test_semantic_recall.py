"""Semantic dialogue recall: owner isolation, edited/deleted messages and fallback."""
from __future__ import annotations

from array import array
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.semantic_recall import SemanticDialogueRecall
from leshy.storage import Storage, utcnow


class FakeMultilingualEncoder:
    """Fake vectors deliberately match paraphrases without shared words."""
    def embed(self, texts):
        results = []
        for text in texts:
            val = str(text).casefold()
            if any(s in val for s in ("видеокарт", "графический ускоритель", "gpu")):
                results.append(array("f", [1, 0, 0]))
            elif any(s in val for s in ("музык", "песен", "песни")):
                results.append(array("f", [0, 1, 0]))
            else:
                results.append(array("f", [0, 0, 1]))
        return results


class SemanticRecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(Path(self.tmp.name) / "leshy.sqlite")
        self.recall = SemanticDialogueRecall(
            self.storage, Path(self.tmp.name),
            encoder_factory=FakeMultilingualEncoder,
        )

    def add(self, session, owner, content):
        with self.storage.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO sessions(id,owner_id,created_at,updated_at) VALUES(?,?,?,?)",
                (session, owner, utcnow(), utcnow()),
            )
            cursor = db.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (session, "user", content, utcnow()),
            )
            return cursor.lastrowid

    def indexed(self, owner):
        while self.recall._index_batch(owner):
            pass

    def test_semantic_paraphrase_from_old_session(self):
        mid = self.add("old", "owner-A", "У меня установлена видеокарта NVIDIA RTX 3070.")
        self.add("music", "owner-A", "Хочу слушать песни через Гусли.")
        self.indexed("owner-A")
        matches = self.recall.find("owner-A", "Какой графический ускоритель у меня?")
        self.assertEqual([item["message_id"] for item in matches], [mid])
        self.assertIn("видеокарта", self.recall.context(
            "owner-A", "Какой GPU установлен?", session_id="new",
        ))
        self.assertEqual(self.recall.find(
            "owner-A", "Какой графический ускоритель у меня?",
            exclude_ids={mid},
        ), [])

    def test_owners_never_mix_even_for_matching_content(self):
        self.add("secret", "owner-B", "Моя видеокарта: не сообщай чужим пользователям.")
        self.add("safe", "owner-A", "Я использую музыку.")
        self.indexed("owner-B")
        self.indexed("owner-A")
        self.assertEqual(self.recall.find(
            "owner-A", "Какая у меня видеокарта?",
        ), [])
        self.assertTrue(self.recall.find(
            "owner-B", "Какой графический ускоритель у меня?",
        ))

    def test_anonymous_web_owner_can_only_recall_its_current_session(self):
        self.add("other-tab", "web:local", "Установлена видеокарта RTX 3070.")
        self.add("my-tab", "web:local", "Слушаю музыку.")
        self.indexed("web:local")
        # Недостаточно знать фиксированное имя web:local: история чужой
        # вкладки не должна попадать в ответ текущей.
        self.assertEqual(
            self.recall.find(
                "web:local", "Какая видеокарта установлена?",
                session_id="my-tab",
            ),
            [],
        )
        self.assertEqual(self.recall.find(
            "web:local", "Какая видеокарта установлена?",
        ), [])

    def test_edit_and_delete_invalidate_recalled_evidence(self):
        mid = self.add("old", "owner-A", "Установлена видеокарта RTX 3070.")
        self.indexed("owner-A")
        self.assertTrue(self.recall.find("owner-A", "Видеокарта компьютера"))
        with self.storage.connection() as db:
            db.execute(
                "UPDATE messages SET content=?,edited_at=? WHERE id=?",
                ("Поставил другую песню", utcnow(), mid),
            )
        self.assertEqual(self.recall.find(
            "owner-A", "Какой графический ускоритель?",
        ), [])
        self.indexed("owner-A")
        self.assertEqual(self.recall.find(
            "owner-A", "Какой графический ускоритель?",
        ), [])
        self.assertTrue(self.recall.find("owner-A", "Музыка и песни"))
        with self.storage.connection() as db:
            db.execute("DELETE FROM sessions WHERE id=?", ("old",))
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM semantic_dialogue_vectors"
            ).fetchone()[0], 0)

    def test_busy_encoder_is_nonblocking_fallback(self):
        self.add("old", "owner-A", "Установлена видеокарта.")
        self.indexed("owner-A")
        self.recall._encoder_lock.acquire()
        try:
            self.assertEqual(self.recall.find(
                "owner-A", "Какой графический ускоритель?",
            ), [])
        finally:
            self.recall._encoder_lock.release()

    def test_no_index_if_no_new_messages_and_status_is_local(self):
        self.add("old", "owner-A", "Установлена видеокарта.")
        self.assertTrue(self.recall._index_batch("owner-A"))
        self.assertFalse(self.recall._index_batch("owner-A"))
        status = self.recall.status()
        self.assertEqual(status["indexed_messages"], 1)
        self.assertTrue(status["local_only"])
        self.assertTrue(status["learning_unchanged"])


if __name__ == "__main__":
    unittest.main()
