from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.knowledge import KnowledgeBase
from leshy.repository_knowledge import validate_record
from leshy.storage import Storage
from leshy.text_integrity import corruption_reason


class KnowledgeTextIntegrityTests(unittest.TestCase):
    def test_corrupt_encodings_are_detected(self):
        examples = (
            "ÐŸÑ€Ð¸Ð²ÐµÑ‚",  # UTF-8 read as Western text
            "РџСЂРёРІРµС‚",  # UTF-8 read as Windows-1251
            "test \ufffd broken",
            r"test \uFFFD broken",
            "text &#65533; broken",
            "bad \x00 content",
        )
        for value in examples:
            with self.subTest(value=value):
                self.assertIsNotNone(corruption_reason(value))
        self.assertIsNone(corruption_reason("Привет, обычный русский текст!"))
        self.assertIsNone(corruption_reason("U+FFFD — код символа замены Unicode."))

    def test_candidate_is_not_inserted_when_damaged(self):
        with TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "knowledge.db")
            knowledge = KnowledgeBase(storage)
            with self.assertRaisesRegex(ValueError, "corrupted knowledge"):
                knowledge.add_candidate(
                    topic="Тема", claim="повреждено \ufffd здесь",
                    source_url="https://example.org/article", source_title="Название",
                )
            with storage.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM knowledge").fetchone()[0], 0)

    def test_existing_damaged_records_are_rejected(self):
        with TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "knowledge.db")
            knowledge = KnowledgeBase(storage)
            record_id = knowledge.add_candidate(
                topic="Проверка", claim="Исходный хороший факт",
                source_url="https://example.org/test", source_title="Нормальный заголовок",
            )
            with storage.connection() as conn:
                conn.execute("UPDATE knowledge SET claim=?,status='trusted' WHERE id=?",
                             ("Неверный текст \ufffd", record_id))
            self.assertEqual(knowledge.quarantine_corrupted(), 1)
            with storage.connection() as conn:
                row = conn.execute("SELECT status FROM knowledge WHERE id=?", (record_id,)).fetchone()
                self.assertEqual(row["status"], "rejected")
            self.assertEqual(knowledge.quarantine_corrupted(), 0)
            self.assertEqual(knowledge.search("Проверка", trusted_only=False), [])

    def test_catalog_rejects_corrupted_nested_source_fields(self):
        record = {
            "schema_version": 1, "id": "unicode-damaged-test", "version": 1,
            "topic": "Кодировка", "claim": "Неповрежденная запись",
            "category": "language", "checked_at": datetime.now(timezone.utc).isoformat(),
            "review_status": "candidate", "data_class": "static",
            "sources": [{
                "url": "https://example.org/utf8",
                "title": "Повреждено \ufffd", "source_family": "example",
            }],
        }
        with self.assertRaisesRegex(ValueError, "corrupted knowledge text"):
            validate_record(record)


if __name__ == "__main__":
    unittest.main()
