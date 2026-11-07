from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from leshy.adaptive_internet_policy import (
    adaptive_research_reason,
    is_explicit_web_request,
    is_offline_creative_request,
)
from leshy.freshness_memory_runtime import is_dynamic_query
from leshy.realtime_chat import RealtimeChatManager
from leshy.research_policy import research_reason


BIRTHDAY_TEXT = (
    "С днём рождения! Пусть в твоей жизни будет много радости, успехов "
    "и приятных сюрпризов. Желаю тебе крепкого здоровья, вдохновения и "
    "достижения всех целей. Пусть каждый день приносит тебе радость "
    "и улыбку! 🎉"
)


class CreativeOfflineRoutingTests(TestCase):
    def test_reported_user_requests_are_not_web_queries(self):
        for text in (
            "Напиши новый поздравительный текст",
            "Напиши новый поздравительный текс",
            "Составь поздравление с днём рождения",
            BIRTHDAY_TEXT,
            "Перепиши этот текст с другими словами",
            "Поздравь коллегу с днём рождения",
            "Напиши поздравление: желаю тебе хороших новостей каждый день",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_offline_creative_request(text))
                self.assertIsNone(adaptive_research_reason(text, [], []))
                self.assertFalse(is_dynamic_query(text))

    def test_old_freshness_marker_would_have_sent_request_to_internet(self):
        self.assertEqual(
            research_reason("Напиши новый поздравительный текст"),
            "требуются актуальные данные",
        )

    def test_explicit_external_research_still_works(self):
        for text in (
            "Проверь в интернете актуальную версию Python",
            "Напиши поздравление на основе последних новостей о запуске ракеты",
            "Напиши поздравление с учетом сегодняшних событий",
            "Напиши пост об уязвимости CVE-2026-119977",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_offline_creative_request(text))
                self.assertIsNotNone(adaptive_research_reason(text, [], []))

    def test_telegram_reply_quoted_web_request_not_equal_user_intent(self):
        quoted = (
            "[Контекст Telegram Reply — приоритетный]\n"
            "Содержимое выбранного сообщения:\n"
            "Проверь в интернете последние новости о новом фильме\n"
            "[Текущий текст пользователя]\n"
            "Напиши новый поздравительный текст"
        )
        self.assertFalse(is_explicit_web_request(quoted))
        self.assertTrue(is_offline_creative_request(quoted))
        self.assertIsNone(adaptive_research_reason(quoted, [], []))


class _Knowledge:
    def search(self, *_args, **_kwargs):
        return []

    def search_candidates(self, *_args, **_kwargs):
        return []


class _App:
    def __init__(self):
        self.knowledge = _Knowledge()
        self._last_internet_route = {}

    def _needs_live_search(self, message):
        return False

    def _memory_context(self, owner_id):
        return ""

    def live_search(self, *_args, **_kwargs):
        raise AssertionError("Творческий запрос не должен вызывать поисковик")


class RealtimeCreativeFinalBarrierTests(TestCase):
    def test_chat_plan_keeps_telegram_creative_request_offline(self):
        manager = RealtimeChatManager(_App())
        plan = manager._search_plan("Напиши новый поздравительный текст")
        self.assertFalse(plan["needed"])
        self.assertEqual(manager.app._last_internet_route["mode"], "локально")

    def test_forced_stale_plan_cannot_reenable_web_search(self):
        manager = RealtimeChatManager(_App())
        forced_plan = {
            "needed": True,
            "reason": "устаревшие локальные данные",
            "stale": [{"claim": "Не подходит"}],
            "trusted": [],
            "candidates": [],
        }
        with patch("leshy.realtime_chat.read_supplied_source", return_value=("", [])):
            messages, sources, attempted = manager._build_context(
                "Напиши новый поздравительный текст",
                "telegram:123",
                [],
                search_plan=forced_plan,
            )
        self.assertFalse(attempted)
        self.assertEqual(sources, [])
        self.assertTrue(any(
            "Интернет-поиск здесь НЕ выполнялся" in item.content
            or "Не выполнялся интернет-поиск" in item.content
            for item in messages
        ))


if __name__ == "__main__":
    import unittest

    unittest.main()
