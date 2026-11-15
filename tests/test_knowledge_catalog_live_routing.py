"""The same gated knowledge fallback must reach standard and streaming chat."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from leshy.realtime_chat import RealtimeChatManager


class _NoKnowledge:
    def search(self, query, *, trusted_only=True, limit=5):
        return []

    def search_candidates(self, query, *, limit=5):
        return []


class CatalogStreamingRegressionTests(unittest.TestCase):
    def test_streaming_route_uses_catalog_candidate_without_claiming_online_verification(self):
        candidate = {
            "topic": "Physics", "claim": "The speed of light is a defined constant.",
            "source_url": "https://www.bipm.org/en/measurement-units",
            "review_status": "candidate",
        }
        calls = []
        app = SimpleNamespace(
            knowledge=_NoKnowledge(),
            _catalog_matches=lambda *args: calls.append(args) or [candidate],
            _needs_live_search=lambda message: False,
            _memory_context=lambda owner_id: "",
            live_search=lambda *args, **kwargs: {"results": [], "error": "offline"},
            _last_internet_route={},
        )
        manager = RealtimeChatManager(app)
        plan = manager._search_plan("Что такое скорость света?")
        self.assertEqual(len(calls), 1)
        self.assertEqual(plan["catalog_matches"], [candidate])
        self.assertEqual(plan["candidates"], [candidate])
        with (
            patch("leshy.realtime_chat.read_supplied_source", return_value=("", [])),
            patch("leshy.realtime_chat.current_clock_context", return_value="Clock available"),
        ):
            context, sources, attempted = manager._build_context(
                "Что такое скорость света?", "owner-a", [], search_plan=plan,
            )
        self.assertTrue(attempted)
        self.assertEqual(sources, [])
        self.assertTrue(any(candidate["claim"] in item.content for item in context))
        self.assertTrue(any("непроверенными candidate" in item.content for item in context))
        self.assertTrue(any("Текущий веб-поиск временно не выполнен" in item.content for item in context))

    def test_creative_request_does_not_read_catalog(self):
        calls = []
        app = SimpleNamespace(
            knowledge=_NoKnowledge(),
            _catalog_matches=lambda *args: calls.append(args) or [],
            _needs_live_search=lambda message: False,
            _last_internet_route={},
        )
        manager = RealtimeChatManager(app)
        plan = manager._search_plan("Напиши стих про погоду сегодня")
        self.assertEqual(plan["catalog_matches"], [])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
