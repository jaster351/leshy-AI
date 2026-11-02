from __future__ import annotations

from datetime import datetime, timezone, timedelta
import unittest

from leshy.temporal_grounding import (
    ground_search_query,
    temporal_grounding,
    temporal_system_instruction,
)


class TemporalGroundingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 12, 57, 0, tzinfo=timezone(timedelta(hours=5)))

    def test_exact_user_weather_phrase_is_grounded_to_current_date(self):
        phrase = "Расскажи погоду В Челябинске на сегодня"
        grounded = ground_search_query(phrase, now=self.now)
        self.assertIn("2026-11-02", grounded)
        self.assertIn("2 ноября 2026", grounded)
        self.assertIn("Челябинске", grounded)

    def test_weather_without_word_today_defaults_to_current_date(self):
        grounded = ground_search_query("Погода в Челябинске", now=self.now)
        self.assertIn("2026-11-02", grounded)

    def test_tomorrow_uses_next_calendar_date(self):
        ground = temporal_grounding("Погода в Челябинске завтра", now=self.now)
        self.assertIsNotNone(ground)
        self.assertEqual(ground.iso_date, "2026-11-03")

    def test_yesterday_uses_previous_calendar_date(self):
        ground = temporal_grounding("Какие новости были вчера?", now=self.now)
        self.assertIsNotNone(ground)
        self.assertEqual(ground.iso_date, "2026-11-01")

    def test_non_dynamic_query_is_not_modified(self):
        phrase = "Объясни принцип работы TCP"
        self.assertEqual(ground_search_query(phrase, now=self.now), phrase)
        self.assertEqual(temporal_system_instruction(phrase, now=self.now), "")

    def test_system_instruction_contains_current_and_target_date(self):
        text = temporal_system_instruction("Расскажи погоду в Челябинске на сегодня", now=self.now)
        self.assertIn("2026-11-02 12:57:00 UTC+05:00", text)
        self.assertIn("Запрошенная относительная дата", text)
        self.assertIn("2 ноября 2026", text)
        self.assertIn("Не выдавай старый прогноз", text)


if __name__ == "__main__":
    unittest.main()
