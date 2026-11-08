from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from leshy.adaptive_internet_policy import (
    adaptive_research_reason,
    is_explicit_web_request,
    ttl_for_context,
)
from leshy.research_policy import (
    external_freshness_requested,
    is_casual_checkin,
    research_reason,
)


class InternetIntentRoutingTests(unittest.TestCase):
    """Регрессионная матрица для Windows/Web/Telegram (тесты вручную запускать после сборки)."""

    def test_casual_and_stable_requests_do_not_force_search(self):
        local = (
            "Чем занят?",
            "Чем ты занят сейчас?",
            "Что ты сейчас делаешь?",
            "Слушай, чем занят?",
            "Леший, чем занят?",
            "Как дела?",
            "Как дела сегодня?",
            "Что у тебя нового?",
            "Какая у тебя версия?",
            "Кто ты?",
            "Привет, Леший!",
            "Напиши новый текст для поздравления",
            "Напиши стих про погоду сегодня",
            "Сочини историю про поездку и билеты",
            "Почему небо голубое?",
            "Что такое закон Ома?",
            "Объясни закон Ома",
            "Что такое налог?",
            "Как работает курс валют?",
            "Что такое WebGPU?",
            "Я закончил работу",
        )
        for query in local:
            with self.subTest(query=query):
                self.assertIsNone(adaptive_research_reason(query, [], []))

    def test_external_current_facts_require_search_without_fresh_trusted_sources(self):
        online = (
            "Какая погода завтра?",
            "Какая цена на бензин?",
            "Какая последняя версия Python?",
            "Какие новые правила ПДД?",
            "Какие новости сегодня?",
            "Кто сейчас президент Франции?",
            "Кто выиграл матч?",
            "Найди ресторан поблизости",
            "Найди гостиницу в центре",
            "Когда следующий рейс в Москву?",
            "Какие противопоказания у препарата?",
            "Сколько стоит билет?",
            "Как дела у Microsoft?",
            "Чем занят президент сейчас?",
            "Напиши поздравление на основе последних новостей",
        )
        for query in online:
            with self.subTest(query=query):
                self.assertIsNotNone(adaptive_research_reason(query, [], []))

    def test_explicit_web_request_overrides_caching_and_creative_intent(self):
        fresh = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "data_class": "dynamic",
            "ttl_seconds": 86400,
        }
        for query in (
            "/search latest Python version",
            "Проверь в интернете последнюю версию Python",
            "Поищи информацию о WebGPU в интернете",
            "Проверь в интернете и напиши поздравление с актуальными фактами",
        ):
            with self.subTest(query=query):
                self.assertTrue(is_explicit_web_request(query))
                self.assertEqual(
                    adaptive_research_reason(query, [fresh], []),
                    "явный запрос интернет-поиска",
                )

    def test_one_reply_does_not_reuse_search_intent_from_quoted_message(self):
        quoted = (
            "Михаил: Проверь в интернете последние новости\n"
            "[Текущий текст пользователя] Привет"
        )
        self.assertTrue(is_casual_checkin(quoted))
        self.assertIsNone(adaptive_research_reason(quoted, [], []))

    def test_substrings_do_not_create_false_freshness(self):
        for query in (
            "Как посчитать оценку качества?",
            "Дискурс и лингвистика",
            "Я закончил работу",
            "Как сделать правильно?",
            "Я прохожу курс математики",
        ):
            with self.subTest(query=query):
                self.assertFalse(external_freshness_requested(query))

    def test_explicit_and_dynamic_still_override_local_intent(self):
        self.assertIsNotNone(adaptive_research_reason("Привет, расскажи последние новости", [], []))
        self.assertIsNotNone(adaptive_research_reason("Что нового у Microsoft?", [], []))
        self.assertEqual(
            research_reason("CVE-2026-119977"),
            "требуются актуальные данные",
        )
        self.assertEqual(
            adaptive_research_reason("CVE-2026-119977", [], []),
            "свежая проверка бюллетеня безопасности",
        )

    def test_dynamic_cache_never_overrides_explicit_request(self):
        now = datetime.now(timezone.utc)
        recent = {
            "checked_at": (now - timedelta(hours=2)).isoformat(),
            "captured_at": now.isoformat(),
            "data_class": "dynamic",
            "ttl_seconds": 86400,
        }
        self.assertIsNone(adaptive_research_reason("Какая последняя версия Python?", [recent], []))
        self.assertEqual(
            adaptive_research_reason("Проверь в интернете последнюю версию Python", [recent], []),
            "явный запрос интернет-поиска",
        )
        old = dict(recent, checked_at=(now - timedelta(days=2)).isoformat())
        self.assertIsNotNone(adaptive_research_reason("Какая последняя версия Python?", [old], []))

    def test_contextual_expiry_for_dynamic_information(self):
        self.assertEqual(ttl_for_context("Курс доллара"), 15 * 60)
        self.assertEqual(ttl_for_context("Работает ли сайт?"), 15 * 60)
        self.assertEqual(ttl_for_context("Какая погода?"), 30 * 60)
        self.assertEqual(ttl_for_context("Ресторан поблизости"), 24 * 60 * 60)
        self.assertEqual(ttl_for_context("Противопоказания"), 7 * 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
