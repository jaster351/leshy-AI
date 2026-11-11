"""Проверки маршрутизации поиска по ссылкам и контексту одной сессии."""
import unittest

from leshy.contextual_web_query import (
    contextual_search_query,
    relevant_url_search_results,
    url_search_query,
)


class ContextualWebQueryTests(unittest.TestCase):
    def test_url_drops_http_prefix_and_tracking_query(self):
        message = (
            "https://www.rockstargames.com/ru/newswire/article/ak3ak31a49a221/"
            "grand-theft-auto-vi-is-now-set-to-launch-november-19-2026?id=1%27 "
            "а что ты на это скажешь?"
        )
        query = url_search_query(message)
        self.assertTrue(query.startswith("site:www.rockstargames.com "))
        self.assertIn("grand theft auto vi", query)
        self.assertIn("november 19 2026", query)
        self.assertNotIn("https", query)
        self.assertNotIn("id=1", query)

    def test_followup_restores_the_previous_question(self):
        rows = [
            {"role": "user", "content": "Зайди на официальный сайт"},
            {"role": "assistant", "content": "Извините, не могу открыть."},
            {"role": "user", "content": "Расскажи, когда выйдет GTA 6. Сколько дней осталось?"},
        ]
        query, forced = contextual_search_query(rows[0]["content"], rows)
        self.assertTrue(forced)
        self.assertIn("GTA 6", query)
        self.assertNotIn("Извините", query)

    def test_followup_to_first_question_restores_topic(self):
        rows = [
            {"role": "user", "content": "и ответить на мой вопрос первоначальный"},
            {"role": "assistant", "content": "Прочтите сайт самостоятельно."},
            {"role": "user", "content": "Зайди на официальный сайт"},
            {"role": "assistant", "content": "Я не знаю."},
            {"role": "user", "content": "Когда выйдет GTA 6 и сколько осталось дней?"},
        ]
        query, forced = contextual_search_query(rows[0]["content"], rows)
        self.assertTrue(forced)
        self.assertIn("GTA 6", query)

    def test_non_followup_does_not_reuse_another_topic(self):
        rows = [
            {"role": "user", "content": "Придумай рецепт супа"},
            {"role": "user", "content": "Когда выйдет GTA 6?"},
        ]
        query, forced = contextual_search_query(rows[0]["content"], rows)
        self.assertEqual(query, "Придумай рецепт супа")
        self.assertFalse(forced)

    def test_unrelated_https_results_filtered(self):
        query = "site:www.rockstargames.com grand theft auto vi november 19 2026"
        rows = [
            {"title": "HTTPS — Википедия", "snippet": "HTTP Secure", "url": "https://wikipedia.org/wiki/HTTPS"},
            {"title": "Grand Theft Auto VI release", "snippet": "launch November 19 2026", "url": "https://www.rockstargames.com/ru/newswire/article/abc/grand-theft-auto-vi"},
        ]
        self.assertEqual(relevant_url_search_results(query, rows), [rows[1]])


if __name__ == "__main__":
    unittest.main()
