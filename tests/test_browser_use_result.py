"""Проверка возврата результата Browser Use вместо пустой квитанции."""
import unittest

from leshy.browser_agent import BrowserAction, BrowserRisk
from leshy.browser_use_adapter import BrowserUseAdapter


class BrowserUseResultTests(unittest.TestCase):
    def setUp(self):
        self.action = BrowserAction(
            "research", "https://www.rockstargames.com/ru/newswire/article/abc",
            BrowserRisk.READ, {},
        )

    def test_short_factual_result_is_not_discarded(self):
        result = BrowserUseAdapter._result_for_user(
            self.action, "GTA VI: 19 ноября 2026 года", ("www.rockstargames.com",),
        )
        self.assertEqual(result.status, "ok")
        self.assertIn("19 ноября 2026", result.message)
        self.assertEqual(result.data["result"], "GTA VI: 19 ноября 2026 года")

    def test_empty_or_generic_reports_are_not_content(self):
        for text in ("", "Готово!", "Browser Use завершил браузерную задачу."):
            with self.subTest(text=text):
                result = BrowserUseAdapter._result_for_user(
                    self.action, text, ("www.rockstargames.com",),
                )
                self.assertEqual(result.status, "failed")

    def test_result_preserves_read_url(self):
        result = BrowserUseAdapter._result_for_user(
            self.action, "Официальный анонс Rockstar: GTA VI выйдет 19 ноября 2026.",
            ("www.rockstargames.com",),
        )
        self.assertEqual(result.data["url"], self.action.url)


if __name__ == "__main__":
    unittest.main()
