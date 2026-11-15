from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.browser_launch_policy import chromium_launch_policy
from leshy.system_browser import _cached_playwright_browser, _playwright_chromium


class ChromiumLaunchPolicyTests(unittest.TestCase):
    def test_ubuntu_without_display_runs_headless(self) -> None:
        policy = chromium_launch_policy(environment={}, system="Linux", effective_uid=1000)
        self.assertTrue(policy.headless)
        self.assertTrue(policy.chromium_sandbox)
        self.assertFalse(policy.can_open_window)

    def test_ubuntu_without_display_ignores_impossible_visible_override(self) -> None:
        policy = chromium_launch_policy(
            environment={"LESHY_BROWSER_HEADLESS": "0"}, system="Linux", effective_uid=1000
        )
        self.assertTrue(policy.headless)

    def test_root_ubuntu_uses_explicit_sandbox_exception(self) -> None:
        policy = chromium_launch_policy(environment={}, system="Linux", effective_uid=0)
        self.assertTrue(policy.root_linux)
        self.assertFalse(policy.chromium_sandbox)
        self.assertIn("--no-sandbox", policy.args)

    def test_linux_with_display_can_open_window(self) -> None:
        policy = chromium_launch_policy(environment={"DISPLAY": ":0"}, system="Linux", effective_uid=1000)
        self.assertFalse(policy.headless)
        self.assertTrue(policy.can_open_window)

    def test_windows_keeps_visible_browser_default(self) -> None:
        policy = chromium_launch_policy(environment={}, system="Windows", effective_uid=-1)
        self.assertFalse(policy.headless)
        self.assertTrue(policy.chromium_sandbox)
        self.assertEqual(policy.args, ())
        self.assertTrue(policy.can_open_window)

    def test_windows_headless_explicit_opt_in(self) -> None:
        policy = chromium_launch_policy(
            environment={"LESHY_BROWSER_HEADLESS": "1"}, system="Windows", effective_uid=-1
        )
        self.assertTrue(policy.headless)


class CachedChromiumTests(unittest.TestCase):
    def test_playwright_chromium_detected_without_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            browser = Path(tmp) / "chromium-1337" / "chrome-linux64" / "chrome"
            browser.parent.mkdir(parents=True)
            browser.write_bytes(b"test executable")
            browser.chmod(0o755)
            detected = _playwright_chromium(Path(tmp))
            self.assertIsNotNone(detected)
            self.assertEqual(detected.name, "Playwright Chromium")
            self.assertEqual(Path(detected.executable), browser)

    def test_override_playwright_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "chromium-1" / "chrome-linux" / "chrome"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"test executable")
            binary.chmod(0o755)
            with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": tmp}):
                detected = _cached_playwright_browser()
            self.assertIsNotNone(detected)
            self.assertEqual(Path(detected.executable), binary)

    def test_missing_playwright_browser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_playwright_chromium(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
