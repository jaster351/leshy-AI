"""Regression cases for salutation contamination of an internet search."""
from __future__ import annotations

import unittest

from leshy.research import SearchResult
from leshy.search_parallel import (
    _relevant_search_results,
    _search_intent_query,
    _topic_words,
)


QUESTION = (
    "Добрый день. Если я хочу настроить ежедневные бэкапы, "
    "то сколько это будет стоить? Размер данных около 380ГБ и постоянно растет."
)


class SearchGreetingRelevanceTests(unittest.TestCase):
    def test_real_backup_question_is_not_searched_as_a_juice_brand(self):
        query = _search_intent_query(QUESTION)
        self.assertTrue(query.startswith("Если я хочу настроить ежедневные бэкапы"))
        self.assertNotIn("добрый", _topic_words(query))
        self.assertNotIn("день", _topic_words(query))

    def test_brand_hits_do_not_count_as_relevant(self):
        unrelated = SearchResult(
            "Добрый — производитель соков",
            "https://example.org/juice",
            "Бренд Добрый: соки и напитки",
        )
        backup = SearchResult(
            "Резервное копирование: стоимость бэкапов",
            "https://example.org/backup",
            "Тарифы хранения для ежедневных резервных копий",
        )
        self.assertEqual(_relevant_search_results(QUESTION, [unrelated, backup]), [backup])

    def test_the_actual_provider_receives_question_without_greeting(self):
        from leshy.search_parallel import RacingSearch

        class Provider:
            queries = []

            def search(self, query, limit=5):
                self.queries.append(query)
                return [
                    SearchResult("Ежедневные бэкапы", "https://example.org/backup", "Стоимость резервного копирования")
                ]

        provider = Provider()
        self.assertEqual(len(RacingSearch(provider).search(QUESTION)), 1)
        self.assertEqual(provider.queries, [_search_intent_query(QUESTION)])

    def test_ordinary_subject_dobry_is_not_stripped(self):
        query = "Сколько стоит сок Добрый?"
        self.assertEqual(_search_intent_query(query), query)
        self.assertEqual(_search_intent_query("Добрый день"), "Добрый день")


if __name__ == "__main__":
    unittest.main()
