"""Owner-scoped long-dialogue continuity regressions, no model or network.

This tests context SELECTION, not that an LLM understood or executed a task.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
import unittest

from leshy.thread_continuity import (
    is_elliptical_followup, normalize_followup, choose_recent_task_anchor,
    continuation_llm_instruction,
)
from leshy.context_history import load_session_context
from leshy.conversation_mode import (
    resolve_conversation_mode, conversation_rows_for_mode,
)


def database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL)"
    )
    return conn


def send(conn, sid, role, text):
    return conn.execute(
        "INSERT INTO messages(session_id,role,content) VALUES(?,?,?)",
        (sid, role, text),
    ).lastrowid


SETTINGS = SimpleNamespace(
    history_limit=lambda: 24, unlimited_history=lambda: False,
)
WORK = "Проверь ошибки Kafka на сервере и подготовь исправление конфигурации"
PRIVATE = "Обсудим музыку и мой новый альбом, без серверных задач"


class ShortFollowupSignals(unittest.TestCase):
    def test_case_and_yo_normalization(self):
        self.assertTrue(is_elliptical_followup("  ЕЩЁ?!  "))

    def test_multispace_normalization(self):
        self.assertEqual(normalize_followup("  И   ЧТО\nДАЛЬШЕ?! "), "и что дальше")

    def test_russian_followup_variants(self):
        for value in ("Дальше", "Продолжай", "Продолжи", "Продолжаем",
                      "А дальше?", "И что дальше?", "А теперь.",
                      "Следующий шаг", "Давай дальше", "Сделай это",
                      "Еще", "Ещё", "Окей", "Да", "Поехали"):
            with self.subTest(text=value):
                self.assertTrue(is_elliptical_followup(value))

    def test_english_followup_variants(self):
        for value in ("Go on", "Continue", "Next"):
            with self.subTest(text=value):
                self.assertTrue(is_elliptical_followup(value))

    def test_substantive_new_task_is_not_followup(self):
        for value in (WORK, PRIVATE, "Начни новый проект Гусли",
                      "Прекрати RDP и запусти проверки сервера",
                      "Можешь исправить ошибку в CI?"):
            with self.subTest(text=value):
                self.assertFalse(is_elliptical_followup(value))

    def test_explicit_switches_never_continuations(self):
        for value in ("/chat", "/support Клиент: сервер недоступен",
                      "Сменим тему", "/work", "Дальше по Гуслям"):
            with self.subTest(text=value):
                self.assertFalse(is_elliptical_followup(value))

    def test_non_string_input_not_interpreted(self):
        for value in (None, b"дальше", 5, ["дальше"], {"text": "дальше"}):
            with self.subTest(text=value):
                self.assertFalse(is_elliptical_followup(value))

    def test_very_long_message_does_not_trigger_history_scan(self):
        self.assertFalse(is_elliptical_followup("Дальше " * 100))

    def test_instruction_contains_no_user_data(self):
        text = continuation_llm_instruction()
        self.assertIn("квитанции", text)
        self.assertNotIn(WORK, text)

    def test_empty_whitespace_is_not_followup(self):
        for text in ("", " ", "\n", "	", "...."):
            self.assertFalse(is_elliptical_followup(text))


class OwnerSessionAnchorTests(unittest.TestCase):
    def setUp(self):
        self.db = database()
        self.addCleanup(self.db.close)

    def test_anchor_latest_substantive_user_not_first_ever(self):
        send(self.db, "A", "user", "Ранее обсуждали Django и настройки сайта")
        later = send(self.db, "A", "user", WORK)
        current = send(self.db, "A", "user", "Дальше")
        got = choose_recent_task_anchor(self.db, "A", current)
        self.assertEqual(got["id"], later)

    def test_anchor_ignores_assistant_claims_of_done(self):
        send(self.db, "A", "user", WORK)
        send(self.db, "A", "assistant", "Всё исправлено, тесты пройдены")
        current = send(self.db, "A", "user", "Дальше")
        got = choose_recent_task_anchor(self.db, "A", current)
        self.assertEqual(got["content"], WORK)

    def test_no_cross_session_content_even_same_owner(self):
        send(self.db, "A", "user", WORK)
        curr = send(self.db, "B", "user", "Дальше")
        self.assertIsNone(choose_recent_task_anchor(self.db, "B", curr))

    def test_future_message_not_used(self):
        curr = send(self.db, "A", "user", "Дальше")
        send(self.db, "A", "user", WORK)
        self.assertIsNone(choose_recent_task_anchor(self.db, "A", curr))

    def test_empty_session_disallowed(self):
        send(self.db, "A", "user", WORK)
        self.assertIsNone(choose_recent_task_anchor(self.db, "", 500))

    def test_invalid_before_identifier_disallowed(self):
        send(self.db, "A", "user", WORK)
        self.assertIsNone(choose_recent_task_anchor(self.db, "A", 0))
        self.assertIsNone(choose_recent_task_anchor(self.db, "A", True))

    def test_bounded_scan_stops_without_guessing(self):
        send(self.db, "A", "user", WORK)
        for _ in range(10):
            send(self.db, "A", "user", "Дальше")
        curr = send(self.db, "A", "user", "Дальше")
        self.assertIsNone(choose_recent_task_anchor(self.db, "A", curr, scan_limit=5))

    def test_anchor_ignores_short_replies(self):
        send(self.db, "A", "user", WORK)
        send(self.db, "A", "user", "Дальше")
        send(self.db, "A", "user", "Окей")
        curr = send(self.db, "A", "user", "Дальше")
        self.assertEqual(choose_recent_task_anchor(self.db, "A", curr)["content"], WORK)

    def test_no_substantive_task_returns_none(self):
        for _ in range(40):
            send(self.db, "A", "user", "Дальше")
        curr = send(self.db, "A", "user", "Продолжай")
        self.assertIsNone(choose_recent_task_anchor(self.db, "A", curr))

    def test_read_only_selector_preserves_row_count(self):
        send(self.db, "A", "user", WORK)
        curr = send(self.db, "A", "user", "Дальше")
        before = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        choose_recent_task_anchor(self.db, "A", curr)
        after = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(before, after)

    def test_selects_nearest_topic_after_new_topic(self):
        send(self.db, "A", "user", WORK)
        send(self.db, "A", "user", PRIVATE)
        curr = send(self.db, "A", "user", "Дальше")
        self.assertEqual(choose_recent_task_anchor(self.db, "A", curr)["content"], PRIVATE)


class FullHistoryAndModeTests(unittest.TestCase):
    def setUp(self):
        self.db = database()
        self.addCleanup(self.db.close)

    def many(self, root=WORK, followup="Дальше", count=35, sid="A"):
        anchor = send(self.db, sid, "user", root)
        send(self.db, sid, "assistant", "Записал исходную задачу, результаты не проверены")
        for number in range(count):
            send(self.db, sid, "user", followup)
            send(self.db, sid, "assistant", f"Промежуточный анализ № {number}")
        current = send(self.db, sid, "user", followup)
        return anchor, current

    def test_restore_root_after_35_continuations(self):
        root, current = self.many()
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        ids = {x["id"] for x in rows}
        self.assertIn(root, ids)
        self.assertEqual(rows[0]["id"], current)

    def test_restore_root_after_180_continuations(self):
        root, _ = self.many(count=180)
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        self.assertIn(root, {x["id"] for x in rows})

    def test_no_semantic_search_spam_on_old_dalshe(self):
        root, _ = self.many(count=75)
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        self.assertLessEqual(len(rows), 25)
        self.assertEqual(sum(x["id"] == root for x in rows), 1)

    def test_work_mode_after_many_continuations(self):
        root, _ = self.many(count=30)
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        self.assertEqual(resolve_conversation_mode("Дальше", rows).kind, "work")
        focused = conversation_rows_for_mode(rows, "work", preserve_task_anchor=True)
        self.assertIn(root, {x["id"] for x in focused})

    def test_personal_mode_after_many_continuations(self):
        root, _ = self.many(root=PRIVATE, count=30)
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        self.assertEqual(resolve_conversation_mode("Дальше", rows).kind, "personal")
        selected = conversation_rows_for_mode(rows, "personal", preserve_task_anchor=True)
        self.assertIn(root, {x["id"] for x in selected})

    def test_support_ticket_not_in_personal_continuation(self):
        send(self.db, "A", "user", "/support Клиент: не работает SSH на VPS")
        send(self.db, "A", "user", PRIVATE)
        for _ in range(26):
            send(self.db, "A", "user", "Дальше")
        send(self.db, "A", "user", "Дальше")
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        kind = resolve_conversation_mode("Дальше", rows).kind
        selected = conversation_rows_for_mode(rows, kind, preserve_task_anchor=True)
        self.assertEqual(kind, "personal")
        self.assertNotIn("Клиент", " ".join(x["content"] for x in selected))

    def test_no_anchor_duplicate_if_recent_explicit_task(self):
        root = send(self.db, "A", "user", WORK)
        send(self.db, "A", "user", "Дальше")
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        self.assertEqual(sum(x["id"] == root for x in rows), 1)

    def test_topic_switch_does_not_restore_old_root(self):
        original, _ = self.many(count=34)
        other = send(self.db, "A", "user", PRIVATE)
        send(self.db, "A", "user", "Дальше")
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        focused = conversation_rows_for_mode(rows, "personal", preserve_task_anchor=True)
        self.assertIn(other, {x["id"] for x in focused})
        self.assertNotIn(original, {x["id"] for x in focused})

    def test_older_anchor_keeps_original_user_role(self):
        root, _ = self.many(count=25)
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        anchor = next(x for x in rows if x["id"] == root)
        self.assertEqual(anchor["role"], "user")

    def test_older_anchor_retains_reverse_chronological_order(self):
        self.many(count=30)
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        self.assertEqual([x["id"] for x in rows], sorted((x["id"] for x in rows), reverse=True))

    def test_current_plain_new_topic_does_not_restore_old_root(self):
        root, _ = self.many(count=27)
        send(self.db, "A", "user", PRIVATE)
        rows = load_session_context(self.db, "A", PRIVATE, SETTINGS)
        self.assertNotIn(root, {x["id"] for x in rows})

    def test_distinct_sessions_never_share_anchor(self):
        root, _ = self.many(count=35, sid="A")
        send(self.db, "B", "user", "Дальше")
        rows = load_session_context(self.db, "B", "Дальше", SETTINGS)
        self.assertNotIn(root, {x["id"] for x in rows})

    def test_initial_question_path_still_works(self):
        root, _ = self.many(count=30)
        send(self.db, "A", "user", "Ответь на мой первоначальный вопрос")
        rows = load_session_context(self.db, "A", "Ответь на мой первоначальный вопрос", SETTINGS)
        self.assertIn(root, {x["id"] for x in rows})

    def test_direct_question_not_given_continuation_instruction(self):
        self.assertFalse(is_elliptical_followup("Какая сейчас дата?"))

    def test_old_assistant_done_claim_is_never_selected_as_task(self):
        root, _ = self.many(count=30)
        rows = load_session_context(self.db, "A", "Дальше", SETTINGS)
        selected = [x for x in rows if x["id"] == root]
        self.assertEqual(len(selected), 1)
        self.assertNotIn("результаты не проверены", selected[0]["content"])


if __name__ == "__main__":
    unittest.main()
