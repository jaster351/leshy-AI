"""Opt-in Kitesurf CDP bridge contracts; no real browsers or network traffic."""
from __future__ import annotations

from contextlib import contextmanager
import json
import types
import unittest
from unittest import mock

from leshy.browser_agent import BrowserAction, BrowserRisk
from leshy.kitesurf_cdp_adapter import (
    KitesurfCdpAdapter, local_kitesurf_endpoint, _target_ok,
)


class FakePage:
    def __init__(self, text="Public page information from a website. " * 3):
        self.url = "https://example.com/read"
        self.text = text
        self.got = []

    def set_default_timeout(self, timeout):
        pass

    def goto(self, url, **kwargs):
        self.url = url
        self.got.append(url)

    def locator(self, selector):
        assert selector == "body"
        return types.SimpleNamespace(inner_text=lambda **kwargs: self.text)

    def title(self):
        return "Page title"


class FakeContext:
    def __init__(self, page, *, route_error=False):
        self.page = page
        self.route_error = route_error
        self.rule = None
        self.closed = False

    def route(self, pattern, callback):
        if self.route_error:
            raise RuntimeError("Fetch interception not implemented")
        self.rule = callback

    def new_page(self):
        assert self.rule is not None, "Navigation must fail closed without request interception"
        return self.page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.options = None
        self.closed = False

    def new_context(self, **kwargs):
        self.options = kwargs
        return self.context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.connected_endpoint = None

    def connect_over_cdp(self, endpoint, **kwargs):
        self.connected_endpoint = endpoint
        return self.browser


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class KitesurfTests(unittest.TestCase):
    def test_endpoint_requires_literal_loopback_and_fixed_cdp_path(self):
        for endpoint, expected in (
            ("ws://127.0.0.1:8787/devtools/browser", "http://127.0.0.1:8787/json/version"),
            ("ws://[::1]:8787/devtools/browser", "http://[::1]:8787/json/version"),
        ):
            with self.subTest(endpoint=endpoint):
                ws, discovery = local_kitesurf_endpoint(endpoint)
                self.assertEqual(ws, endpoint)
                self.assertEqual(discovery, expected)
        for invalid in (
            "wss://cloud.example/devtools/browser",
            "ws://127.1:8787/devtools/browser",
            "ws://127.5.1.2:8787/devtools/browser",
            "ws://[::1%eth0]:8787/devtools/browser",
            "ws://2130706433:8787/devtools/browser",
            "ws://localhost:8787/devtools/browser",
            "ws://127.0.0.1:80/devtools/browser",
            "ws://user:pass@127.0.0.1:8787/devtools/browser",
            "ws://127.0.0.1:8787/devtools/browser?token=secret",
            "ws://127.0.0.1:8787/json/version",
            "http://127.0.0.1:8787/devtools/browser",
            "ws://10.0.0.8:8787/devtools/browser",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    local_kitesurf_endpoint(invalid)

    def test_target_allowlist_rejects_private_and_redirect_tricks(self):
        allowed = ("example.com",)
        self.assertTrue(_target_ok("https://example.com/a", allowed))
        self.assertTrue(_target_ok("https://news.example.com/a", allowed))
        for invalid in (
            "https://evil-example.com", "https://example.com.evil.invalid",
            "http://127.0.0.1/", "http://127.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/", "file:///etc/passwd", "javascript:alert(1)",
            "https://example.com:8844/", "https://example.com@evil.invalid/",
            "https://example.com.local/", "https://example.com.internal/",
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(_target_ok(invalid, allowed))

    def test_no_network_or_cdp_when_not_explicitly_enabled(self):
        adapter = KitesurfCdpAdapter(endpoint="")
        self.assertFalse(adapter.available())
        with mock.patch.object(adapter, "_probe") as probe:
            result = adapter.execute(BrowserAction(
                "research", "https://example.com", BrowserRisk.READ,
            ))
        self.assertEqual(result.status, "unavailable")
        probe.assert_not_called()

    def test_read_requires_healthy_kitesurf_discovery(self):
        adapter = KitesurfCdpAdapter("ws://127.0.0.1:8787/devtools/browser")
        with mock.patch.object(adapter, "available", return_value=True), mock.patch.object(
            adapter, "_probe", return_value=False
        ):
            result = adapter.execute(BrowserAction(
                "research", "https://example.com", BrowserRisk.READ,
            ))
        self.assertEqual(result.status, "unavailable")

    def test_read_only_cdp_and_request_interception(self):
        page = FakePage()
        context = FakeContext(page)
        browser = FakeBrowser(context)
        chrome = FakeChromium(browser)

        @contextmanager
        def fake_playwright():
            yield FakePlaywright(chrome)

        adapter = KitesurfCdpAdapter("ws://127.0.0.1:8787/devtools/browser")
        with mock.patch.object(adapter, "available", return_value=True), mock.patch.object(
            adapter, "_probe", return_value=True,
        ), mock.patch("playwright.sync_api.sync_playwright", side_effect=fake_playwright):
            result = adapter.execute(BrowserAction(
                "research", "https://example.com/read", BrowserRisk.READ,
                {"allowed_hosts": ("example.com",)},
            ))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data["browser"], "kitesurf-cdp")
        self.assertIn("Public page information", result.data["result"])
        self.assertEqual(chrome.connected_endpoint, "ws://127.0.0.1:8787/devtools/browser")
        self.assertEqual(page.got, ["https://example.com/read"])
        self.assertEqual(browser.options["service_workers"], "block")
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

        public = types.SimpleNamespace(request=types.SimpleNamespace(
            url="https://news.example.com/article",
        ), continue_=mock.Mock(), abort=mock.Mock())
        context.rule(public)
        public.continue_.assert_called_once()
        public.abort.assert_not_called()

        private = types.SimpleNamespace(request=types.SimpleNamespace(
            url="http://169.254.169.254/latest/meta-data/",
        ), continue_=mock.Mock(), abort=mock.Mock())
        context.rule(private)
        private.abort.assert_called_once()
        private.continue_.assert_not_called()

    def test_incompatible_cdp_does_not_navigate_or_get_cookies(self):
        page = FakePage()
        context = FakeContext(page, route_error=True)
        browser = FakeBrowser(context)
        chrome = FakeChromium(browser)

        @contextmanager
        def fake_playwright():
            yield FakePlaywright(chrome)

        adapter = KitesurfCdpAdapter("ws://127.0.0.1:8787/devtools/browser")
        with mock.patch.object(adapter, "available", return_value=True), mock.patch.object(
            adapter, "_probe", return_value=True,
        ), mock.patch("playwright.sync_api.sync_playwright", side_effect=fake_playwright):
            result = adapter.execute(BrowserAction(
                "research", "https://example.com/read", BrowserRisk.READ,
                {"allowed_hosts": ("example.com",)},
            ))
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(page.got, [])
        self.assertTrue(browser.closed)

    def test_mutations_never_reach_cdp_even_if_adapter_called_directly(self):
        adapter = KitesurfCdpAdapter("ws://127.0.0.1:8787/devtools/browser")
        with mock.patch.object(adapter, "_probe") as probe:
            result = adapter.execute(BrowserAction(
                "research", "https://example.com/pay", BrowserRisk.FINANCIAL,
            ))
        self.assertEqual(result.status, "unsupported")
        probe.assert_not_called()

    def test_challenge_does_not_claim_page_was_read(self):
        page = FakePage("Verify you are human: CAPTCHA")
        context = FakeContext(page)
        chrome = FakeChromium(FakeBrowser(context))

        @contextmanager
        def fake_playwright():
            yield FakePlaywright(chrome)

        adapter = KitesurfCdpAdapter("ws://127.0.0.1:8787/devtools/browser")
        with mock.patch.object(adapter, "available", return_value=True), mock.patch.object(
            adapter, "_probe", return_value=True,
        ), mock.patch("playwright.sync_api.sync_playwright", side_effect=fake_playwright):
            result = adapter.execute(BrowserAction(
                "research", "https://example.com/read", BrowserRisk.READ,
                {"allowed_hosts": ("example.com",)},
            ))
        self.assertEqual(result.status, "needs_user_action")


class BrowserPlatformKitesurfRoutingTests(unittest.TestCase):
    def test_direct_url_prefers_explicit_local_engine_without_browser_use(self):
        from leshy.browser_platform_runtime import BrowserPlatform, BrowserConversationIntent
        platform = object.__new__(BrowserPlatform)
        platform.kitesurf = mock.Mock()
        platform.kitesurf.available.return_value = True
        platform.kitesurf.execute.return_value = mock.Mock(
            status="ok", data={"result": "Site contents"}, message="read",
        )
        platform.agent = mock.Mock()
        platform._remember_results = mock.Mock()
        answer, sources = platform.research_url(
            BrowserConversationIntent("research_url", "Изучи эту страницу",
                                      "https://example.com/read"),
            owner_id="web:owner", session_id="session-1",
        )
        self.assertEqual(answer, "Site contents")
        self.assertEqual(sources[0]["url"], "https://example.com/read")
        platform.kitesurf.execute.assert_called_once()
        platform.agent.execute.assert_not_called()
        platform._remember_results.assert_called_once()

    def test_unavailable_kitesurf_falls_back_to_existing_browser_agent(self):
        from leshy.browser_platform_runtime import BrowserPlatform, BrowserConversationIntent
        platform = object.__new__(BrowserPlatform)
        platform.kitesurf = mock.Mock()
        platform.kitesurf.available.return_value = True
        platform.kitesurf.execute.return_value = mock.Mock(
            status="unavailable", data={}, message="CDP incompatible",
        )
        platform.agent = mock.Mock()
        platform.agent.execute.return_value = mock.Mock(
            status="ok", data={"result": "Chromium contents"}, message="read",
        )
        platform._remember_results = mock.Mock()
        answer, _ = platform.research_url(
            BrowserConversationIntent("research_url", "Посмотри", "https://example.com/read"),
            owner_id="web:owner", session_id="session-1",
        )
        self.assertEqual(answer, "Chromium contents")
        platform.agent.execute.assert_called_once()

    def test_challenge_must_not_be_retried_through_another_engine(self):
        from leshy.browser_platform_runtime import BrowserPlatform, BrowserConversationIntent
        platform = object.__new__(BrowserPlatform)
        platform.kitesurf = mock.Mock()
        platform.kitesurf.available.return_value = True
        platform.kitesurf.execute.return_value = mock.Mock(
            status="needs_user_action", data={}, message="CAPTCHA required",
        )
        platform.agent = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "CAPTCHA"):
            platform.research_url(
                BrowserConversationIntent("research_url", "Прочитай", "https://example.com/read"),
                owner_id="web:owner", session_id="session-1",
            )
        platform.agent.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
