"""Keyboard-layout suggestion regression scenarios (no network/model required)."""
import unittest

from leshy.keyboard_layout import (
    LayoutStreamFormatter,
    layout_answer,
    layout_context,
    suggest_keyboard_layout,
)


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

    def test_capslock_and_initial_capital(self):
        self.assertEqual(suggest_keyboard_layout("GHBDTN").corrected, "ПРИВЕТ")
        self.assertEqual(suggest_keyboard_layout("Ghbdtn").corrected, "Привет")

    def test_common_weather_and_time_questions(self):
        self.assertEqual(suggest_keyboard_layout("rfrfz gjujlf").corrected, "какая погода")
        self.assertEqual(suggest_keyboard_layout("crjkmrj dhtvtyb").corrected, "сколько времени")

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

    def test_non_stream_answer_always_has_one_notice(self):
        prefix = "Похоже, вы имели в виду: «привет»."
        self.assertEqual(layout_answer("ghbdtn", "Привет!"), prefix + "\n\nПривет!")
        self.assertEqual(layout_answer("ghbdtn", prefix + "\n\nПривет!"),
                         prefix + "\n\nПривет!")
        self.assertEqual(layout_answer("hello", "Hello!"), "Hello!")
        self.assertEqual(layout_answer("ghbdtn", ""), "")

    def test_stream_prefix_sent_once_before_normal_answer(self):
        formatter = LayoutStreamFormatter("ghbdtn")
        out = formatter.push("При") + formatter.push("вет!") + formatter.finish()
        self.assertEqual(out, "Похоже, вы имели в виду: «привет».\n\nПривет!")

    def test_stream_deduplicates_model_generated_notice_across_chunks(self):
        formatter = LayoutStreamFormatter("ghbdtn")
        self.assertEqual(formatter.push("Пох"), "")
        self.assertEqual(formatter.push("оже, вы имели в виду: «привет»."), "")
        self.assertEqual(formatter.push("\n\nПривет!"),
                         "Похоже, вы имели в виду: «привет».\n\nПривет!")
        self.assertEqual(formatter.finish(), "")

    def test_no_layout_no_prefix_for_technical_text(self):
        formatter = LayoutStreamFormatter("ssh root@server")
        self.assertEqual(formatter.push("Connected."), "Connected.")
        self.assertEqual(formatter.finish(), "")

    def test_chat_calls_formatter_after_persisting_raw_question(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        app = (root / "src" / "leshy" / "app.py").read_text(encoding="utf-8")
        realtime = (root / "src" / "leshy" / "realtime_chat.py").read_text(encoding="utf-8")
        self.assertIn("answer = layout_answer(message, answer)", app)
        self.assertIn("stream_formatter = LayoutStreamFormatter(message)", realtime)
        self.assertIn("answer = layout_answer(message, answer)", realtime)
        self.assertLess(app.index("INSERT INTO messages("), app.index("answer = layout_answer(message, answer)"))
        self.assertLess(realtime.index("INSERT INTO messages("),
                        realtime.index("stream_formatter = LayoutStreamFormatter(message)"))

    def test_hint_preserves_original_and_explains(self):
        hint = layout_context("ghbdtn")
        self.assertIn("ghbdtn", hint)
        self.assertIn("привет", hint)
        self.assertEqual(layout_context("ssh root@host"), "")


if __name__ == "__main__":
    unittest.main()
