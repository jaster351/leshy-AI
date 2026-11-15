from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from leshy.freshness_cache_runtime import _search_fresh_dynamic
from leshy.knowledge import KnowledgeBase
from leshy.storage import Storage


class FreshnessCacheRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "leshy.db")
        self.knowledge = KnowledgeBase(self.storage)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_weather_is_reused_from_leshybrain(self):
        self.knowledge.add_candidate(
            topic="Погода в Челябинске сегодня",
            claim="Температура около -2 °C",
            source_url="https://weather.example/current",
            source_type="live-search-snippet",
            data_class="dynamic",
            checked_at=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=1800,
        )
        rows = _search_fresh_dynamic(self.knowledge, "Какая погода в Челябинске сегодня?", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["claim"], "Температура около -2 °C")

    def test_expired_weather_is_not_reused_as_fresh(self):
        self.knowledge.add_candidate(
            topic="Погода в Челябинске сегодня",
            claim="Старый прогноз",
            source_url="https://weather.example/expired",
            source_type="live-search-snippet",
            data_class="dynamic",
            checked_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            ttl_seconds=1800,
        )
        rows = _search_fresh_dynamic(self.knowledge, "Какая погода в Челябинске сегодня?", limit=5)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
