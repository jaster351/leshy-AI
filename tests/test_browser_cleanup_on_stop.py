"""Browser windows must close on success, navigation timeout and Web Stop.

Use fake Browser Use classes: these tests must not open a user's Chrome tabs
or require an installed browser or external website.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import sys

from leshy.browser_agent import BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk
from leshy.browser_use_adapter import BrowserUseAdapter
from leshy.playwright_browser_adapter import PlaywrightBrowserAdapter


class BrowserCleanupTests(unittest.TestCase):
    def _exercise(self, agent_class, stop_event=None):
        close_calls = []
        begin = threading.Event()

        class FakeBrowser:
            def __init__(self, **kwargs):
                self.keep_alive = kwargs.get("keep_alive", False)
            async def stop(self):
                close_calls.append("stop")

        fake_module = ModuleType("browser_use")
        fake_module.Agent = agent_class
        fake_module.Browser = FakeBrowser
        fake_module.Tools = lambda exclude_actions: SimpleNamespace(excluded=exclude_actions)
        with TemporaryDirectory() as tmp:
            with patch.dict("sys.modules", {"browser_use": fake_module}):
                policy = SimpleNamespace(
                    headless=True, can_open_window=False, args=[],
                    chromium_sandbox=False, root_linux=False,
                )
                with patch("leshy.browser_use_adapter.chromium_launch_policy", return_value=policy), \
                     patch("leshy.browser_use_adapter.ensure_system_browser",
                           return_value=SimpleNamespace(name="mock", executable="chromium")), \
                     patch("leshy.browser_use_adapter.profile_may_be_in_use", return_value=False), \
                     patch.dict("os.environ", {"LESHY_BRAIN_DIR": tmp}):
                    adapter = BrowserUseAdapter(llm=object(), headless=True)
                    action = BrowserAction(
                        "research", "https://github.com/example/repo",
                        BrowserRisk.READ, {"allowed_hosts": ("github.com",)},
                    )
                    if stop_event is None:
                        with self.assertRaises((TimeoutError, ValueError)):
                            asyncio.run(adapter._run(action))
                    else:
                        def setter():
                            self.assertTrue(begin.wait(timeout=5))
                            stop_event.set()
                        def begin_run():
                            begin.set()
                        agent_class.on_start = staticmethod(begin_run)
                        watcher = threading.Thread(target=setter, daemon=True)
                        watcher.start()
                        try:
                            with self.assertRaises(asyncio.CancelledError):
                                adapter._run_sync(adapter._run(action), cancel_event=stop_event)
                        finally:
                            watcher.join(timeout=3)
        self.assertEqual(close_calls, ["stop"])

    def test_timeout_closes_owned_browser(self):
        class FakeAgent:
            def __init__(self, **kwargs):
                pass
            async def run(self, **kwargs):
                raise TimeoutError("Timeout 30000ms exceeded: github.com")
        self._exercise(FakeAgent)

    def test_agent_constructor_error_closes_owned_browser(self):
        class FakeAgent:
            def __init__(self, **kwargs):
                raise ValueError("Agent could not initialize")
        self._exercise(FakeAgent)

    def test_cancelled_browser_use_task_closes_owned_browser(self):
        class FakeAgent:
            on_start = None
            def __init__(self, **kwargs):
                pass
            async def run(self, **kwargs):
                self.on_start()
                await asyncio.Event().wait()
        self._exercise(FakeAgent, threading.Event())

    def test_cancelled_read_does_not_open_fallback(self):
        cancel = threading.Event()
        cancel.set()

        class Stub:
            adapter_id = "stub"
            called = False
            def available(self):
                return True
            def execute(self, action):
                self.called = True
                return BrowserActionResult("ok", "unexpected")

        first = Stub()
        second = Stub()
        agent = BrowserAgent([first, second])
        result = agent.execute(BrowserAction(
            "research", "https://github.com", BrowserRisk.READ,
            {"_cancel_event": cancel},
        ))
        self.assertEqual(result.status, "cancelled")
        self.assertFalse(first.called)
        self.assertFalse(second.called)

    def test_playwright_navigation_uses_commit_not_blocking_domcontentloaded(self):
        class Page:
            url = "https://github.com/example/repo"
            def __init__(self):
                self.goto_args = None
                self.wait_args = None
            def goto(self, url, **kwargs):
                self.goto_args = (url, kwargs)
            def wait_for_load_state(self, state, **kwargs):
                self.wait_args = (state, kwargs)
                raise TimeoutError("DOM never settles")

        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"LESHY_BRAIN_DIR": tmp}):
            adapter = PlaywrightBrowserAdapter(headless=True)
            page = Page()
            adapter._goto(page, page.url, ("github.com",))
        self.assertEqual(page.goto_args[1]["wait_until"], "commit")
        self.assertEqual(page.wait_args[1]["timeout"], 5000)


if __name__ == "__main__":
    unittest.main()
