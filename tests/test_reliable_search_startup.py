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
            ["SearxngSearch", "DuckDuckGoHtmlSearch", "BingRssSearch", "DdgsSearch"],
        )

    def test_configuration_is_idempotent(self):
        app = _already_created_app()
        _configure_app_search(app)
        first = app.research.search_provider
        _configure_app_search(app)
        self.assertIs(app.research.search_provider, first)
        self.assertIsInstance(first, RacingSearch)
        self.assertEqual(first.hedge_delay_seconds, 0.0)
        self.assertEqual(
            sum(isinstance(item, ChromiumSearch) for item in first.providers), 0,
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
            patch.object(ChromiumSearch, "search", side_effect=AssertionError("browser search must not run")),
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
            ["CustomProvider", "DuckDuckGoHtmlSearch", "BingRssSearch", "DdgsSearch"],
        )

    def test_env_cannot_enable_browser_search(self):
        app = _already_created_app()
        with patch.dict("os.environ", {"LESHY_CHROMIUM_SEARCH_ENABLED": "1"}):
            _configure_app_search(app)
        providers = app.research.search_provider.providers
        self.assertFalse(any(isinstance(item, ChromiumSearch) for item in providers))
        self.assertEqual(app.research.search_provider.hedge_delay_seconds, 0.0)

    def test_old_browser_provider_is_removed_on_reconfiguration(self):
        app = SimpleNamespace(
            research=SimpleNamespace(
                search_provider=FallbackSearch(
                    ChromiumSearch(),
                    SearxngSearch("http://127.0.0.1:8080", timeout=3),
                ),
            ),
        )
        _configure_app_search(app)
        providers = app.research.search_provider.providers
        self.assertFalse(any(isinstance(item, ChromiumSearch) for item in providers))
        self.assertTrue(any(isinstance(item, SearxngSearch) for item in providers))
        self.assertTrue(any(isinstance(item, DuckDuckGoHtmlSearch) for item in providers))


    def test_stale_configured_chain_is_migrated_without_duplicate_cluster_peer(self):
        app = _already_created_app()
        _configure_app_search(app)
        configured = app.research.search_provider
        # Emulate a live process whose previously configured provider had a browser.
        configured.providers = (ChromiumSearch(), *configured.providers)
        self.assertTrue(configured._leshy_reliable_web)
        _configure_app_search(app)
        migrated = app.research.search_provider
        self.assertIsNot(migrated, configured)
        self.assertFalse(any(isinstance(item, ChromiumSearch) for item in migrated.providers))
        self.assertEqual(
            sum(type(item).__name__ == "ClusterPeerSearch" for item in migrated.providers), 1,
        )
        _configure_app_search(app)
        self.assertIs(app.research.search_provider, migrated)

    def test_browser_constructor_not_called_for_default_search(self):
        app = _already_created_app()
        with patch.object(ChromiumSearch, "__init__", side_effect=AssertionError("browser constructed")):
            _configure_app_search(app)
        self.assertFalse(any(isinstance(item, ChromiumSearch) for item in app.research.search_provider.providers))


if __name__ == "__main__":
    import unittest

    unittest.main()
