from __future__ import annotations

import unittest

from leshy.cinema_research import (
    cinema_instruction,
    cinema_queries,
    cinema_query,
    cinema_search,
)


class CinemaResearchTests(unittest.TestCase):
    def test_contextual_whats_new_in_almaz(self):
        rows = [
            {"role": "user", "content": "что нового?"},
            {"role": "assistant", "content": "Расписание"},
            {"role": "user", "content": "Мягкий кинотеатр Алмаз в Челябинске"},
        ]
        plan = cinema_query("что нового?", rows, day="2026-11-04")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.venue, "Мягкий кинотеатр Алмаз")
        self.assertEqual(plan.city, "Челябинск")
        self.assertTrue(plan.now)
        self.assertTrue(plan.upcoming)
        self.assertEqual(len(cinema_queries(plan)), 2)

    def test_no_leak_from_other_topic(self):
        self.assertIsNone(cinema_query("что нового?", [
            {"role": "user", "content": "Проверим сервер по IP"}
        ]))
        self.assertIsNone(cinema_query("что нового?"))

    def test_different_cinema_city_kept(self):
        plan = cinema_query("Какие сеансы в кинотеатре Фокус в Екатеринбурге?", day="2026-11-04")
        self.assertEqual(plan.city, "Екатеринбург")
        self.assertTrue(plan.now)
        self.assertFalse(plan.upcoming)
        self.assertIn("Екатеринбург", cinema_queries(plan)[0])

    def test_upcoming_only(self):
        plan = cinema_query("Что скоро выйдет в Мягком кинотеатре Алмаз в Челябинске?", day="2026-11-04")
        self.assertTrue(plan.upcoming)
        self.assertFalse(plan.now)

    def test_search_keeps_source_receipts(self):
        class Search:
            calls = []
            def live_search(self, query, *, limit, reason):
                self.calls.append(query)
                if "скоро" in query:
                    raise TimeoutError("future search timeout")
                return {"results": [{"title": "Афиша", "url": "https://example.org/cinema", "snippet": "Фильм 19:00"}]}
        app = Search()
        plan = cinema_query("Что нового в кинотеатре Алмаз в Челябинске", day="2026-11-04")
        result = cinema_search(app, plan)
        self.assertEqual(len(app.calls), 2)
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("timeout", result["error"])

    def test_guidance_rejects_invented_prices_seats(self):
        plan = cinema_query("Что нового в кинотеатре Алмаз в Челябинске")
        guidance = cinema_instruction(plan)
        self.assertIn("Свободные места", guidance)
        self.assertIn("Никогда не придумывай", guidance)


if __name__ == "__main__":
    unittest.main()
