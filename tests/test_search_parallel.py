"""Independent internet-search providers and bounded parallel fallback."""
from __future__ import annotations

import io
import threading
import time
import unittest
from unittest.mock import patch

from leshy.research import SearchResult, SearchUnavailable
from leshy.search_parallel import BingRssSearch, RacingSearch


class _Provider:
    def __init__(self, *, delay=0.0, results=(), failure=None):
        self.delay = delay
        self.results = list(results)
        self.failure = failure
        self.calls = 0

    def search(self, query, limit=5):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.failure:
            raise self.failure
        return self.results


class SearchParallelRegressionTests(unittest.TestCase):
    def test_fast_browser_answer_does_not_wake_http_fallback(self):
        browser = _Provider(delay=0.02, results=[
            SearchResult("Chrome", "https://example.org/chrome", "Из браузерной выдачи")
        ])
        http = _Provider(delay=0.01, results=[
            SearchResult("HTTP", "https://example.org/http", "Резерв")
        ])
        result = RacingSearch(browser, http, hedge_delay_seconds=0.15).search("поиск")
        self.assertEqual(result[0].title, "Chrome")
        self.assertEqual(browser.calls, 1)
        self.assertEqual(http.calls, 0)

    def test_slow_browser_activates_independent_http_fallback(self):
        browser = _Provider(delay=1.2, results=[
            SearchResult("Поздно", "https://example.org/later", "Большая задержка")
        ])
        http = _Provider(delay=0.02, results=[
            SearchResult("Своевременно", "https://example.org/fast", "Проверенный результат")
        ])
        started = time.monotonic()
        result = RacingSearch(
            browser, http, hedge_delay_seconds=0.1, deadline_seconds=2.0,
        ).search("поиск")
        self.assertEqual(result[0].title, "Своевременно")
        self.assertLess(time.monotonic() - started, 0.65)

    def test_slow_ddgs_does_not_block_fast_independent_provider(self):
        slow = _Provider(delay=1.6, failure=RuntimeError("timeout"))
        fast = _Provider(delay=0.02, results=[
            SearchResult("АльфаСтрахование", "https://example.org/insurance", "Официальная информация"),
        ])
        started = time.monotonic()
        rows = RacingSearch(slow, fast).search("условия страхования")
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5)
        self.assertEqual(rows[0].url, "https://example.org/insurance")
        self.assertEqual(slow.calls, 1)
        self.assertEqual(fast.calls, 1)

    def test_failed_source_does_not_stop_next_success(self):
        a = _Provider(failure=RuntimeError("HTTP 429"))
        b = _Provider(results=[])
        c = _Provider(results=[SearchResult("Источник", "https://example.net", "Факт")])
        rows = RacingSearch(a, b, c).search("проверить факт")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Источник")

    def test_http_200_with_no_results_is_not_success(self):
        empty = _Provider(results=[])
        failing = _Provider(failure=TimeoutError())
        with self.assertRaises(SearchUnavailable):
            RacingSearch(empty, failing).search("неизвестный запрос")

    def test_first_result_must_have_http_url(self):
        private = _Provider(results=[
            SearchResult("Неправильный URL", "file:///etc/passwd", "не должно попасть в ответ")
        ])
        verified = _Provider(results=[SearchResult("Ответ", "https://example.org", "верный")])
        self.assertEqual(RacingSearch(private, verified).search("вопрос")[0].title, "Ответ")

    def test_bing_rss_parsing_results_with_original_links(self):
        body = ("""<?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0"><channel>
              <item><title>Результат &amp; источник</title>
                <link>https://example.org/page</link>
                <description>Содержание &lt;b&gt;важно&lt;/b&gt;</description></item>
              <item><title>повтор</title><link>https://example.org/page</link></item>
            </channel></rss>""").encode()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, size=-1): return body[:size]
        class Opener:
            def open(self, req, *, timeout):
                self.assert_called = True
                if not req.full_url.startswith("https://www.bing.com/search?"):
                    raise ValueError("unexpected search endpoint")
                return Response()
        with patch("leshy.search_parallel.request.build_opener", return_value=Opener()):
            results = BingRssSearch().search("test", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Результат & источник")
        self.assertEqual(results[0].url, "https://example.org/page")
        self.assertEqual(results[0].snippet, "Содержание важно")

    def test_non_rss_captcha_is_not_reported_as_search_result(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, size=-1): return b"<html>CAPTCHA</html>"
        class Opener:
            def open(self, req, *, timeout): return Response()
        with patch("leshy.search_parallel.request.build_opener", return_value=Opener()):
            self.assertEqual(BingRssSearch().search("captcha"), [])

    def test_parallel_deadline_is_finite(self):
        slow = _Provider(delay=1.5, results=[])
        started = time.monotonic()
        with self.assertRaises(SearchUnavailable):
            RacingSearch(slow, deadline_seconds=1.0).search("тайм-аут")
        self.assertLess(time.monotonic() - started, 1.3)


if __name__ == "__main__":
    unittest.main()
