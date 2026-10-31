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

    def test_telegram_hides_think_in_clickable_spoiler(self):
        rendered = _telegram_answer_html("<think>скрытая мысль</think>Готовый ответ")
        self.assertIn("<tg-spoiler>скрытая мысль</tg-spoiler>", rendered)
        self.assertIn("Готовый ответ", rendered)
        self.assertNotIn("<think>", rendered)

    def test_web_ui_has_hidden_reasoning_and_image_progress(self):
        path = Path(__file__).resolve().parents[1] / "web" / "ui-enhancements.js"
        source = path.read_text(encoding="utf-8")
        self.assertIn("think-block", source)
        self.assertIn("details", source)
        self.assertIn("/v1/image/job?job_id=", source)
        self.assertIn("generated-image", source)

    def test_web_ui_exposes_learning_controls(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        runtime = (root / "web" / "variant3.js").read_text(encoding="utf-8")
        self.assertIn('data-section="learning"', html)
        self.assertIn('id="learningSwitch"', html)
        self.assertIn("learningQuickToggle", runtime)
        self.assertIn("Самообучение: ", runtime)
        self.assertIn("/v1/learning", runtime)

    def test_web_ui_recovers_core_button_handlers_after_js_failure(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "web" / "variant3.js").read_text(encoding="utf-8")
        self.assertIn("function bindCoreControls()", runtime)
        self.assertIn("window.addEventListener('error'", runtime)
        self.assertIn("rebind after error", runtime)
        for control in ("heroSend", "chatSend", "learningSwitch", "researchQueue", "modelDownload"):
            self.assertIn(control, runtime)

    def test_pending_chat_message_cannot_stretch_to_full_screen(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "web" / "ui-enhancements.js").read_text(encoding="utf-8")
        self.assertIn("#messages .msg.user", source)
        self.assertIn("height:auto!important", source)
        self.assertIn("max-width:min(78%,720px)!important", source)
        self.assertIn("function addPendingMessage()", source)
        self.assertIn("pending.remove()", source)
        self.assertIn("pending-dots", source)


if __name__ == "__main__":
    unittest.main()
