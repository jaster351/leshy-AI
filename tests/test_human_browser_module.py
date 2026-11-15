from pathlib import Path
import tempfile
import unittest

from leshy.human_browser_module import BrowserAction, HumanBrowserModule, HumanSystemBrowser
from leshy.modules import ModuleRequest


class HumanBrowserModuleTests(unittest.TestCase):
    def test_manifest_is_standalone_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            browser = HumanSystemBrowser(
                profile_dir=Path(tmp) / "profile",
                screenshots_dir=Path(tmp) / "shots",
                headless=True,
            )
            module = HumanBrowserModule(browser=browser)
            self.assertEqual(module.manifest.name, "human-browser")
            self.assertGreaterEqual(module.manifest.priority, 700)
            self.assertIn("Edge/Chrome/Chromium", module.manifest.description)

    def test_handles_only_explicit_browser_events(self):
        module = HumanBrowserModule(browser=object())
        self.assertTrue(module.can_handle(ModuleRequest("", metadata={"event": "human_browser_inspect"})))
        self.assertTrue(module.can_handle(ModuleRequest("", metadata={"event": "human_browser_run"})))
        self.assertFalse(module.can_handle(ModuleRequest("обычный чат", metadata={})))

    def test_accepts_only_http_and_https(self):
        self.assertEqual(HumanSystemBrowser.validate_url("https://example.com"), "https://example.com")
        self.assertEqual(HumanSystemBrowser.validate_url("http://example.com/a"), "http://example.com/a")
        for value in ("file:///etc/passwd", "javascript:alert(1)", "ftp://example.com", "example.com"):
            with self.assertRaises(ValueError):
                HumanSystemBrowser.validate_url(value)

    def test_normalizes_supported_actions(self):
        actions = HumanSystemBrowser.normalize_actions(
            [
                {"kind": "fill", "selector": "#ip", "value": "203.0.113.10"},
                {"kind": "click", "selector": "button[type=submit]"},
                {"kind": "wait", "wait_ms": 1500},
                BrowserAction("press", "input[name=q]", "Enter"),
            ]
        )
        self.assertEqual([item.kind for item in actions], ["fill", "click", "wait", "press"])
        self.assertEqual(actions[0].value, "203.0.113.10")

    def test_rejects_unknown_or_targetless_actions(self):
        with self.assertRaises(ValueError):
            HumanSystemBrowser.normalize_actions([{"kind": "script", "value": "alert(1)"}])
        with self.assertRaises(ValueError):
            HumanSystemBrowser.normalize_actions([{"kind": "click"}])

    def test_captcha_is_user_action_not_stealth_bypass(self):
        self.assertTrue(HumanSystemBrowser._needs_user_action("Verify you are human - CAPTCHA"))
        self.assertTrue(HumanSystemBrowser._needs_user_action("Подтвердите, что вы человек"))
        self.assertFalse(HumanSystemBrowser._needs_user_action("Обычная страница результата"))


if __name__ == "__main__":
    unittest.main()
