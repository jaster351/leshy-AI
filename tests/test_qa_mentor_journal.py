from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.qa_mentor_journal import QAMentorJournal


class QAMentorJournalTests(unittest.TestCase):
    def test_new_journal_is_empty_and_contains_no_demo_dialogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = QAMentorJournal(Path(tmp))
            result = journal.list_recent()
            self.assertEqual(result["items"], [])
            self.assertEqual(result["count"], 0)
            self.assertIsNone(result["last_activity_at"])

    def test_receipt_is_persistent_and_roles_are_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = QAMentorJournal(Path(tmp))
            row = journal.append_real_exchange(
                role="qa", request="Проверь правило",
                answer="Проверяю", session_id="qa-session-1",
                started_at="2026-11-25T01:00:00+00:00",
                elapsed_ms=75, status="answered",
            )
            self.assertEqual(row["author"], "QA-тестировщик")
            self.assertEqual(row["responder"], "Леший")
            self.assertEqual(row["source"], "live_api")
            self.assertEqual(row["verdict"], "not_evaluated")
            restored = QAMentorJournal(Path(tmp)).list_recent()["items"]
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0]["id"], row["id"])
            self.assertEqual(restored[0]["answer"], "Проверяю")

    def test_failed_call_is_not_a_pass_or_successful_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = QAMentorJournal(Path(tmp))
            row = journal.append_real_exchange(
                role="mentor", request="Сохрани правило",
                answer="успех", started_at="2026-11-25T01:00:00+00:00",
                elapsed_ms=5, status="error", error_type="LLMUnavailable",
            )
            self.assertEqual(row["author"], "AI-наставник")
            self.assertEqual(row["answer"], "")
            self.assertEqual(row["status"], "error")
            self.assertEqual(row["verdict"], "not_evaluated")
            self.assertEqual(row["error_type"], "LLMUnavailable")

    def test_secret_values_are_masked_before_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = QAMentorJournal(Path(tmp))
            row = journal.append_real_exchange(
                role="mentor",
                request="password=abc123 token:secretvalue",
                answer="Bearer abcdefghijklmnop",
                started_at="2026-11-25T01:00:00+00:00",
                elapsed_ms=0, status="answered",
            )
            raw = journal.path.read_text(encoding="utf-8")
            for secret in ("abc123", "secretvalue", "abcdefghijklmnop"):
                self.assertNotIn(secret, raw)
            self.assertIn("[скрыто]", raw)
            self.assertEqual(row["status"], "answered")

    def test_unknown_role_and_status_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = QAMentorJournal(Path(tmp))
            kwargs = dict(
                request="hello", started_at="2026-11-25T01:00:00+00:00",
                elapsed_ms=0, status="answered",
            )
            with self.assertRaises(ValueError):
                journal.append_real_exchange(role="creator", **kwargs)
            with self.assertRaises(ValueError):
                journal.append_real_exchange(role="qa", status="pass",
                                             request="hello", started_at="now", elapsed_ms=0)
            self.assertFalse(journal.path.exists())


if __name__ == "__main__":
    unittest.main()
