"""Keyboard-layout suggestion regression scenarios (no network/model required)."""
import unittest

from leshy.keyboard_layout import layout_context, suggest_keyboard_layout


class KeyboardLayoutTests(unittest.TestCase):
    def test_greeting(self):
        suggestion = suggest_keyboard_layout("ghbdtn")
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion.corrected, "привет")
        self.assertIn("Похоже, вы имели в виду", suggestion.notice)

    def test_question(self):
        suggestion = suggest_keyboard_layout("rfr ltkf")
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion.corrected, "как дела")

    def test_original_russian(self):
        self.assertIsNone(suggest_keyboard_layout("привет"))
        self.assertIsNone(suggest_keyboard_layout("как дела"))

    def test_technical_input_not_rewritten(self):
        for text in (
            "ssh root@192.168.1.1", "git checkout main", "/etc/hosts",
            "https://example.org", "C:\\Windows\\System32",
            "hello world", "Qwen3-8B", "192.168.1.1:22",
            "`ghbdtn`", "ghbdtn привет", "12345678",
        ):
            with self.subTest(text=text):
                self.assertIsNone(suggest_keyboard_layout(text))

    def test_hint_preserves_original_and_explains(self):
        hint = layout_context("ghbdtn")
        self.assertIn("ghbdtn", hint)
        self.assertIn("привет", hint)
        self.assertEqual(layout_context("ssh root@host"), "")


if __name__ == "__main__":
    unittest.main()
