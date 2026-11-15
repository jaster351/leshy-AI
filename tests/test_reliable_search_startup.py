from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from leshy.reliable_web_runtime import (
    DuckDuckGoHtmlSearch, _configure_app_search,
)
from leshy.search_parallel import BingRssSearch, RacingSearch
from leshy.search_chromium import ChromiumSearch
from leshy.research import (
    DdgsSearch, FallbackSearch, SearchResult, SearchUnavailable, SearxngSearch,
)


def _already_created_app():
    """Входной app уже создан Web server'ом: __init__ не вызывается повторно."""
    original = FallbackSearch(
        SearxngSearch("http://127.0.0.1:8080", timeout=3),
        DdgsSearch(timeout=3),
    )
    return SimpleNamespace(research=SimpleNamespace(search_provider=original))


class ReliableSearchStartupTests(TestCase):
    def test_already_created_app_gets_html_fallback(self):
        app = _already_created_app()
        _configure_app_search(app)
        self.assertEqual(
            [type(item).__name__ for item in app.research.search_provider.providers],
            ["ChromiumSearch", "SearxngSearch", "DuckDuckGoHtmlSearch", "BingRssSearch", "DdgsSearch"],
        )

    def test_configuration_is_idempotent(self):
        app = _already_created_app()
        _configure_app_search(app)
        first = app.research.search_provider
        _configure_app_search(app)
        self.assertIs(app.research.search_provider, first)
        self.assertIsInstance(first, RacingSearch)
        self.assertEqual(first.hedge_delay_seconds, 1.4)
        self.assertEqual(
            sum(isinstance(item, ChromiumSearch) for item in first.providers), 1,
        )
        self.assertEqual(
            sum(isinstance(item, DuckDuckGoHtmlSearch) for item in first.providers), 1,
        )
        self.assertEqual(
            sum(isinstance(item, BingRssSearch) for item in first.providers), 1,
        )

    def test_slow_or_unavailable_searxng_does_not_prevent_html_result(self):
        app = _already_created_app()
        _configure_app_search(app)
        fixture = [SearchResult(
            "Python documentation",
            "https://docs.python.org/3/",
            "Latest Python documentation.",
        )]
        with (
            patch.object(ChromiumSearch, "search", return_value=[]),
            patch.object(
                SearxngSearch, "search",
                side_effect=SearchUnavailable("WinError 10061: connection refused"),
            ) as local,
            patch.object(DuckDuckGoHtmlSearch, "search", return_value=fixture) as html,
            patch.object(BingRssSearch, "search", return_value=[]),
            patch.object(DdgsSearch, "search", return_value=[]) as slow,
        ):
            result = app.research.search_provider.search("Python latest release")
        self.assertEqual(result, fixture)
        local.assert_called_once()
        html.assert_called_once()
        slow.assert_called_once()

    def test_existing_nondefault_provider_is_preserved(self):
        class CustomProvider:
            def search(self, query, limit=5):
                return []

        app = SimpleNamespace(
            research=SimpleNamespace(
                search_provider=FallbackSearch(CustomProvider(), DdgsSearch()),
            ),
        )
        _configure_app_search(app)
        self.assertEqual(
            [type(item).__name__ for item in app.research.search_provider.providers],
            ["ChromiumSearch", "CustomProvider", "DuckDuckGoHtmlSearch", "BingRssSearch", "DdgsSearch"],
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
