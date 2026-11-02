from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from leshy.freshness_memory_runtime import (
    _TelegramFreshnessProxy,
    _fallback_instruction,
    _migrate_legacy_dynamic,
    _remember_dynamic_results,
    _search_stale_dynamic,
    is_dynamic_query,
    ttl_for_query,
)
from leshy.knowledge import KnowledgeBase
from leshy.realtime_chat import StreamEvent
from leshy.research import ResearchEngine
from leshy.storage import Storage


class _NoSearch:
    def search(self, query: str, limit: int = 5):
        return []


class _App:
    def __init__(self, knowledge):
        self.knowledge = knowledge


class _Delegate:
    def stream(self, *args, **kwargs):
        yield StreamEvent("activity", {
            "kind": "freshness",
            "phase": "stale",
            "text": "В базе старые данные.",
        })
        yield StreamEvent("activity", {
            "kind": "web_search",
            "phase": "searching",
            "text": "Проверяю интернет…",
        })
        yield StreamEvent("delta", {"text": "финальный поток"})
        yield StreamEvent("done", {"answer": "Финальный ответ", "sources": []})


class FreshnessMemoryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "leshy.db")
        self.knowledge = KnowledgeBase(self.storage)

    def tearDown(self):
        self.tmp.cleanup()

    def _old_iso(self, hours: int = 24) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    def test_weather_is_dynamic_and_has_short_ttl(self):
        self.assertTrue(is_dynamic_query("Расскажи погоду в Челябинске на сегодня"))
        self.assertFalse(is_dynamic_query("Объясни принцип работы TCP"))
        self.assertEqual(ttl_for_query("Погода в Челябинске"), 1800)

    def test_stale_dynamic_knowledge_is_found_but_fresh_is_not(self):
        self.knowledge.add_candidate(
            topic="Погода в Челябинске сегодня",
            claim="Температура -5 °C",
            source_url="https://weather.example/old",
            data_class="dynamic",
            checked_at=self._old_iso(24),
            ttl_seconds=900,
        )
        self.knowledge.add_candidate(
            topic="Погода в Челябинске сегодня",
            claim="Температура -2 °C",
            source_url="https://weather.example/fresh",
            data_class="dynamic",
            checked_at=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=3600,
        )

        stale = _search_stale_dynamic(self.knowledge, "Какая погода в Челябинске сегодня?", limit=5)
        claims = [row["claim"] for row in stale]
        self.assertIn("Температура -5 °C", claims)
        self.assertNotIn("Температура -2 °C", claims)
        self.assertTrue(stale[0].get("freshness_label"))

    def test_live_search_result_is_remembered_as_dynamic_with_checked_at(self):
        engine = ResearchEngine(self.storage, self.knowledge, _NoSearch())
        snippet = "Сегодня в Челябинске температура около -2 °C, ожидается снег."
        claims = engine._extract_candidate_claims(snippet)
        if not claims:
            claims = [snippet[:420]]
        self.knowledge.add_candidate(
            topic="Погода в Челябинске сегодня",
            claim=claims[0],
            source_url="https://weather.example/current",
            source_type="live-search-snippet",
        )

        changed = _remember_dynamic_results(engine, "Погода в Челябинске сегодня", {
            "results": [{
                "title": "Погода",
                "url": "https://weather.example/current",
                "snippet": snippet,
            }]
        })
        self.assertGreaterEqual(changed, 1)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT data_class,checked_at,ttl_seconds FROM knowledge WHERE source_url=? ORDER BY id DESC LIMIT 1",
                ("https://weather.example/current",),
            ).fetchone()
        self.assertEqual(row["data_class"], "dynamic")
        self.assertTrue(row["checked_at"])
        self.assertEqual(int(row["ttl_seconds"]), 1800)

    def test_legacy_weather_live_search_is_migrated_from_static(self):
        self.knowledge.add_candidate(
            topic="Расскажи погоду в Челябинске на сегодня",
            claim="Старый прогноз",
            source_url="https://weather.example/legacy",
            source_type="live-search-snippet",
        )
        changed = _migrate_legacy_dynamic(_App(self.knowledge))
        self.assertEqual(changed, 1)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT data_class,checked_at,ttl_seconds FROM knowledge WHERE source_url=?",
                ("https://weather.example/legacy",),
            ).fetchone()
        self.assertEqual(row["data_class"], "dynamic")
        self.assertTrue(row["checked_at"])
        self.assertEqual(int(row["ttl_seconds"]), 1800)

    def test_fallback_instruction_requires_explicit_stale_warning(self):
        text = _fallback_instruction([{
            "claim": "Вчера было -5 °C",
            "source_url": "https://weather.example/old",
            "freshness_label": "01.11.2026 12:00",
        }])
        self.assertIn("актуальные данные найти не удалось", text)
        self.assertIn("могут быть устаревшими", text)
        self.assertIn("01.11.2026 12:00", text)

    def test_telegram_proxy_shows_status_then_keeps_final_answer_clean(self):
        events = list(_TelegramFreshnessProxy(_Delegate()).stream("weather"))
        deltas = [event.data.get("text") for event in events if event.type == "delta"]
        done = [event for event in events if event.type == "done"][-1]
        self.assertEqual(deltas, ["В базе старые данные.", "\n\nПроверяю интернет…"])
        self.assertEqual(done.data["answer"], "Финальный ответ")
        self.assertNotIn("финальный поток", "".join(deltas))


if __name__ == "__main__":
    unittest.main()
