from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from leshy.research import SearchResult, SearchUnavailable
from leshy.search_parallel import RacingSearch
from leshy.search_chromium import (
    ChromiumSearch, SearchChallenge, _clean_result, _is_challenge,
    _open_context, _search_profile, chromium_search_status,
)


class _Element:
    def __init__(self, *, href, title, description):
        self.href, self.title, self.description = href, title, description

    def get_attribute(self, name, timeout=800):
        return self.href if name == "href" else None

    def inner_text(self, timeout=800):
        return self.title

    def locator(self, selector):
        return SimpleNamespace(inner_text=lambda **kwargs: self.description)


class _Links:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def nth(self, index):
        return self.rows[index]


class _Page:
    url = "https://www.bing.com/search?q=%D0%B1%D0%B5%D0%BB%D0%BA%D0%B0"

    def __init__(self, *, challenge=False, status=200):
        self.challenge = challenge
        self.http_status = status
        self.rows = _Links([_Element(
            href="https://example.org/squirrel", title="Белка", description="Описание белки из настоящего источника",
        )])
        self.visited = ""

    def set_default_timeout(self, timeout):
        self.timeout = timeout

    def goto(self, url, **kwargs):
        self.visited = url
        return SimpleNamespace(status=self.http_status)

    def title(self):
        return "Security challenge" if self.challenge else "Поиск Bing"

    def locator(self, selector):
        if selector == "body":
            return SimpleNamespace(
                inner_text=lambda **kwargs: "Verify you are human" if self.challenge else "Белка — животное",
            )
        return self.rows

    def evaluate(self, expression):
        assert expression == "navigator.userAgent"
        return "Mozilla/5.0 Chrome/133.0.0.0"


class _Context:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False

    def cookies(self):
        return [{"name": "consent", "value": "yes"}]

    def close(self):
        self.closed = True


class _PW:
    def __enter__(self):
        return SimpleNamespace(chromium=object())

    def __exit__(self, *_):
        return False


def _playwright_modules():
    root = ModuleType("playwright")
    root.__path__ = []
    sync = ModuleType("playwright.sync_api")
    sync.sync_playwright = lambda: _PW()
    return {"playwright": root, "playwright.sync_api": sync}


class ChromiumSearchTests(unittest.TestCase):
    def test_search_profile_is_separate_from_user_browser(self):
        with TemporaryDirectory() as tmp:
            path = _search_profile(tmp)
            self.assertEqual(path, Path(tmp) / "browser" / "search-chromium-profile")
            captured = {}
            class Chromium:
                def launch_persistent_context(self, **kwargs):
                    captured.update(kwargs)
                    return object()
            with patch("leshy.search_chromium.ensure_system_browser",
                       return_value=SimpleNamespace(name="Edge", executable="C:/Edge/msedge.exe")), patch(
                "leshy.search_chromium.chromium_launch_policy",
                return_value=SimpleNamespace(can_open_window=True, chromium_sandbox=True, args=()),
            ):
                _open_context(SimpleNamespace(chromium=Chromium()), root=path, headless=True)
            self.assertEqual(captured["user_data_dir"], str(path))
            self.assertEqual(captured["executable_path"], "C:/Edge/msedge.exe")
            self.assertTrue(captured["headless"])
            self.assertFalse("user_agent" in captured)  # Browser sends its real UA.
            self.assertFalse("storage_state" in captured)  # No imported cookies.
            self.assertTrue(captured["accept_downloads"] is False)

    def test_real_page_result_with_native_ua_and_persistent_cookies(self):
        with TemporaryDirectory() as tmp:
            page = _Page()
            context = _Context(page)
            with patch.dict(sys.modules, _playwright_modules()), patch(
                "leshy.search_chromium._open_context",
                return_value=(context, "Microsoft Edge"),
            ):
                rows = ChromiumSearch(brain_dir=tmp).search("белка", limit=3)
            self.assertEqual(rows, [SearchResult(
                "Белка", "https://example.org/squirrel",
                "Описание белки из настоящего источника",
            )])
            self.assertTrue(page.visited.startswith("https://www.bing.com/search?q="))
            self.assertTrue(context.closed)  # Chromium stores cookies at close.
            status = chromium_search_status()
            self.assertEqual(status["cookie_count"], 1)
            self.assertIn("Chrome/", status["user_agent"])
            self.assertEqual(status["state"], "ok")

    def test_challenge_cannot_be_reported_as_search_result(self):
        with TemporaryDirectory() as tmp:
            page = _Page(challenge=True)
            context = _Context(page)
            with patch.dict(sys.modules, _playwright_modules()), patch(
                "leshy.search_chromium._open_context",
                return_value=(context, "Microsoft Edge"),
            ):
                with self.assertRaises(SearchChallenge):
                    ChromiumSearch(brain_dir=tmp).search("новости")
            self.assertTrue(context.closed)
            self.assertEqual(chromium_search_status()["state"], "needs_human_verification")

    def test_http_429_is_challenge_and_captcha_topic_is_not(self):
        self.assertTrue(_is_challenge("Bing", "", 429))
        self.assertTrue(_is_challenge("Security challenge", "", 200))
        self.assertFalse(_is_challenge("Bing", "Как работают CAPTCHA и способы доступности", 200))

    def test_search_url_filter_rejects_internal_links_and_ad_tracking(self):
        for url in (
            "file:///etc/passwd", "http://localhost:8765/api", "http://127.0.0.1",
            "http://192.168.1.1/", "https://bing.com/search?q=secret",
            "https://user:pw@example.org/", "https://example.org/a#fragment",
        ):
            with self.subTest(url=url):
                self.assertIsNone(_clean_result(url, "Результат", "Описание"))
        self.assertIsNotNone(_clean_result(
            "https://example.org/guide", "Ссылка", "Описание",
        ))

    def test_browser_captcha_falls_back_to_independent_search_without_bypass(self):
        browser = SimpleNamespace(search=Mock(side_effect=SearchChallenge("manual only")))
        backup = SimpleNamespace(search=Mock(return_value=[
            SearchResult("Рабочий источник", "https://example.org/result", "Найдено"),
        ]))
        rows = RacingSearch(browser, backup, deadline_seconds=2.0, hedge_delay_seconds=0.1).search("факт")
        self.assertEqual(rows[0].title, "Рабочий источник")
        browser.search.assert_called_once()
        backup.search.assert_called_once()


if __name__ == "__main__":
    unittest.main()
