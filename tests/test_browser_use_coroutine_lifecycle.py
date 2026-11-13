"""Regression: browser-use coroutine is allocated only after runner starts.

No browser, model, or network dependency. Not executed locally by automation.
"""
from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

from leshy.browser_use_adapter import BrowserUseAdapter


class BrowserUseCoroutineLifecycleTests(unittest.TestCase):
    def test_runner_start_failure_does_not_allocate_coroutine(self) -> None:
        created = []

        async def work():
            return "ok"

        def factory():
            created.append(True)
            return work()

        with patch.object(threading.Thread, "start", side_effect=RuntimeError("thread unavailable")):
            async def caller():
                with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                    BrowserUseAdapter._run_sync(factory)

            asyncio.run(caller())

        self.assertEqual(created, [])

    def test_no_running_loop_executes_once(self) -> None:
        calls = []

        async def work():
            calls.append(1)
            return "ok"

        self.assertEqual(BrowserUseAdapter._run_sync(lambda: work()), "ok")
        self.assertEqual(calls, [1])

    def test_existing_loop_uses_worker_without_reusing_coroutine(self) -> None:
        calls = []

        async def work():
            calls.append(threading.current_thread().name)
            return "ok"

        async def caller():
            return BrowserUseAdapter._run_sync(lambda: work())

        self.assertEqual(asyncio.run(caller()), "ok")
        self.assertEqual(calls, ["leshy-browser-use"])


if __name__ == "__main__":
    unittest.main()
