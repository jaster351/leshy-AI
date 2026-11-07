from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebChatEnterRegressionTests(unittest.TestCase):
    """Не отправлять/останавливать один запрос дважды при нажатии Enter."""

    def test_primary_composer_prevents_enter_default(self):
        source = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("e.preventDefault();sendFrom($(id))", source)

    def test_recovery_layer_skips_already_handled_enter(self):
        source = (ROOT / "web" / "variant3.js").read_text(encoding="utf-8")
        self.assertIn("if(event.defaultPrevented || event.isComposing) return;", source)

    def test_recovery_layer_still_handles_fallback_enter(self):
        source = (ROOT / "web" / "variant3.js").read_text(encoding="utf-8")
        self.assertIn("event.preventDefault();", source)
        self.assertIn("sendSafe(input);", source)


if __name__ == "__main__":
    unittest.main()
