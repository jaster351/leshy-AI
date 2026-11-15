"""Owner-scoped memory freshness, duplicate consolidation and learning switch."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from leshy.intelligent_memory import IntelligentMemoryEngine
from leshy.learning_control import LearningControl
from leshy.storage import Storage, utcnow


class MemoryConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Storage(Path(self.temp.name) / "brain.sqlite3")
        self.memory = IntelligentMemoryEngine(self.storage)
        self.learning = LearningControl(self.storage, default_enabled=False)

    def add_memory(self, owner, text, expires, fingerprint):
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO memories(owner_id,kind,content,importance,source,"
                "created_at,expires_at,fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                (owner, "preference", text, 0.95, "manual", utcnow(), expires, fingerprint),
            )

    def test_expired_memory_is_not_injected_into_context(self):
        expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.add_memory("web:one", "NGINX secret obsolete configuration", expired, "exp")
        self.add_memory("web:one", "NGINX active configuration", None, "active")
        self.assertEqual(
            [m.content for m in self.memory.retrieve("web:one", "NGINX configuration")],
            ["NGINX active configuration"],
        )

    def test_memory_is_strictly_scoped_by_owner(self):
        self.add_memory("web:one", "Linux administration blue", None, "a")
        self.add_memory("web:two", "Linux administration red", None, "b")
        found = self.memory.retrieve("web:one", "Linux administration")
        self.assertEqual([m.content for m in found], ["Linux administration blue"])

    def test_punctuation_variants_of_autosaved_preference_are_deduplicated(self):
        one = self.memory.capture_if_useful("web:one", "Я предпочитаю тёмную тему.")
        two = self.memory.capture_if_useful("web:one", "Я предпочитаю тёмную тему!")
        other = self.memory.capture_if_useful("web:two", "Я предпочитаю тёмную тему!")
        self.assertIsNotNone(one)
        self.assertEqual(one, two)
        self.assertNotEqual(one, other)

    def test_retrieval_consolidates_legacy_duplicate_records(self):
        self.add_memory("web:one", "Нужен Linux сервер.", None, "legacy-a")
        self.add_memory("web:one", "Нужен Linux сервер!", None, "legacy-b")
        self.assertEqual(len(self.memory.retrieve("web:one", "Linux сервер")), 1)

    def test_learning_off_keeps_old_memory_visible_without_new_autosave(self):
        self.learning.set_enabled(True)
        self.memory.context("web:one", "Я предпочитаю локальную модель")
        self.assertEqual(len(self.memory.retrieve("web:one", "локальную модель")), 1)
        self.learning.set_enabled(False)
        self.memory.context("web:one", "Я предпочитаю удалённую модель")
        records = self.memory.retrieve("web:one", "модель")
        self.assertEqual(len(records), 1)
        self.assertIn("локальную", records[0].content)


if __name__ == "__main__":
    unittest.main()
