from __future__ import annotations

from pathlib import Path
import unittest

from leshy.persona import LESHY_IDENTITY_PROMPT, with_leshy_identity
from leshy.llm import ChatMessage
from leshy.telegram import _telegram_answer_html


class PresentationTests(unittest.TestCase):
    def test_identity_is_leshy_not_base_model(self):
        messages = with_leshy_identity([ChatMessage("user", "Привет")])
        self.assertEqual(messages[0].role, "system")
        self.assertIn("Ты — Леший", messages[0].content)
        self.assertIn("Qwen", LESHY_IDENTITY_PROMPT)
        self.assertEqual(messages[-1].content, "Привет")

    def test_telegram_does_not_expose_private_think_reasoning(self):
        rendered = _telegram_answer_html("<think>скрытая мысль</think>Готовый ответ")
        self.assertEqual(rendered, "Готовый ответ")
        self.assertNotIn("скрытая мысль", rendered)
        self.assertNotIn("<think>", rendered)

    def test_web_ui_has_hidden_reasoning_and_image_progress(self):
        path = Path(__file__).resolve().parents[1] / "web" / "ui-enhancements.js"
        source = path.read_text(encoding="utf-8")
        self.assertIn("think-block", source)
        self.assertIn("details", source)
        self.assertIn("/v1/image/job?job_id=", source)
        self.assertIn("generated-image", source)


if __name__ == "__main__":
    unittest.main()
