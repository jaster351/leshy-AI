from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest

from leshy.contextual_web_query import contextual_search_query
from leshy.fact_recheck import (
    grounded_release_from_context,
    is_factual_challenge,
    prior_factual_question,
    release_date_from_page,
    source_release_fact,
)
from leshy.semantic_research_router import choose_research


class _OfflineApp:
    llm = None


def _history(*, link=False):
    rows = [
        {"role": "user", "content": "Ты ошибаешься"},
        {"role": "assistant", "content": "GTA VI уже вышла в 2025 году, 147 дней назад."},
    ]
    if link:
        rows.extend([
            {"role": "user", "content": "Проверь официальный сайт https://www.rockstargames.com/VI"},
            {"role": "assistant", "content": "Дата релиза была в 2025 году."},
        ])
    rows.append({"role": "user", "content": "Когда выйдет GTA VI?"})
    return rows


class FactRecheckTests(unittest.TestCase):
    def test_short_objections_are_fact_challenges(self):
        for prompt in (
            "Ты ошибаешься", "ты не прав", "Я не прав?",
            "Это неправда!", "Это не так", "Перепроверь",
            "Точно?", "Откуда ты это взял?",
            "Проверь ещё раз", "Ты перепутал!",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(is_factual_challenge(prompt, _history()))

    def test_no_auto_search_on_emotional_objection(self):
        rows = [
            {"role": "user", "content": "Я не прав?"},
            {"role": "assistant", "content": "Мне кажется, это забавно."},
        ]
        self.assertFalse(is_factual_challenge("Я не прав?", rows))

    def test_prior_user_question_does_not_derive_from_wrong_model_claim(self):
        self.assertEqual(prior_factual_question(_history()), "Когда выйдет GTA VI?")
        query, needs_search = contextual_search_query("Ты ошибаешься", _history())
        self.assertIn("Когда выйдет GTA VI?", query)
        self.assertTrue(needs_search)
        self.assertNotIn("147", query)
        self.assertNotIn("2025", query)

    def test_followup_after_official_link_rereads_given_page(self):
        query, needs_search = contextual_search_query("Я не прав?", _history(link=True))
        self.assertEqual(query, "Я не прав?")
        self.assertFalse(needs_search)

    def test_direct_url_is_not_replaced_with_unrelated_search(self):
        text = "Проверь https://www.rockstargames.com/VI"
        query, needs_search = contextual_search_query(text, _history())
        self.assertEqual(query, text)
        self.assertFalse(needs_search)
        self.assertFalse(choose_research(_OfflineApp(), text)[0])

    def test_explicit_independent_search_preserved(self):
        text = "https://www.rockstargames.com/VI сравни с другими источниками"
        _, needs_search = contextual_search_query(text, _history())
        self.assertTrue(needs_search)

    def test_release_date_separate_from_preload_news(self):
        page = (
            "ПРЕДЗАГРУЗКА НАЧАЛАСЬ ВЫХОДИТ 19 НОЯБРЯ 2026 ГОДА "
            "Предзагрузка GTA VI начнётся 10 ноября 2026 года. "
            "Новость опубликована 5 ноября 2026 года."
        )
        self.assertEqual(release_date_from_page(page), date(2026, 11, 19))
        reply = source_release_fact(
            "Когда выйдет GTA VI?",
            page, "https://www.rockstargames.com/VI",
            today=date(2026, 11, 15),
        )
        self.assertIn("19 ноября 2026", reply)
        self.assertIn("4 дня", reply)
        self.assertNotIn("2025", reply)

    def test_explicit_conflicting_dates_not_overruled(self):
        page = "ВЫХОДИТ 19 НОЯБРЯ 2026 ГОДА. ВЫХОДИТ 20 НОЯБРЯ 2026 ГОДА"
        self.assertIsNone(release_date_from_page(page))

    def test_grounded_release_uses_actual_supplied_page(self):
        url = "https://www.rockstargames.com/VI"
        note = (
            f"Прочитана исходная веб-страница: {url}.\n"
            "--- НАЧАЛО НЕДОВЕРЕННОГО ТЕКСТА СТРАНИЦЫ ---\n"
            "Предзагрузка 10 ноября. ВЫХОДИТ 19 НОЯБРЯ 2026 ГОДА "
            "Новость 5 ноября.\n"
            "--- КОНЕЦ ТЕКСТА СТРАНИЦЫ ---"
        )
        answer = grounded_release_from_context(
            "ты ошибаешься", _history(),
            [SimpleNamespace(content=note)],
            [{"title": "Rockstar", "url": url}],
        )
        self.assertIsNotNone(answer)
        self.assertIn("19 ноября 2026", answer)
        self.assertIn(url, answer)

    def test_release_year_not_inferred_from_unmarked_publication_date(self):
        self.assertIsNone(release_date_from_page(
            "Новость от 10 ноября 2026. Предзагрузка GTA VI началась сегодня."
        ))


if __name__ == "__main__":
    unittest.main()
