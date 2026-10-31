from __future__ import annotations

import unittest

from leshy.adaptive_backend import AdaptiveLLM
from leshy.llm import ChatMessage
from leshy.persona import LESHY_IDENTITY_PROMPT, with_leshy_identity


class _CaptureLLM:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def chat(self, messages: list[ChatMessage]) -> str:
        self.messages = list(messages)
        return "Привет. Я Леший."


class PersonaTests(unittest.TestCase):
    def test_identity_prompt_names_leshy_and_forbids_qwen_identity(self):
        self.assertIn("Ты — Леший", LESHY_IDENTITY_PROMPT)
        self.assertIn("Никогда сам не представляйся как Qwen", LESHY_IDENTITY_PROMPT)
        self.assertIn("на «Вы»", LESHY_IDENTITY_PROMPT)

    def test_identity_is_prepended_without_mutating_original_messages(self):
        original = [ChatMessage("user", "Привет")]
        prepared = with_leshy_identity(original)
        self.assertEqual(len(original), 1)
        self.assertEqual(prepared[0].role, "system")
        self.assertIn("Леший", prepared[0].content)
        self.assertEqual(prepared[1].content, "Привет")

    def test_adaptive_backend_always_receives_leshy_identity_first(self):
        backend = _CaptureLLM()
        llm = AdaptiveLLM([("cpu-native", backend)])
        answer = llm.chat([ChatMessage("user", "привет")])
        self.assertEqual(answer, "Привет. Я Леший.")
        self.assertGreaterEqual(len(backend.messages), 2)
        self.assertEqual(backend.messages[0].role, "system")
        self.assertIn("Ты — Леший", backend.messages[0].content)
        self.assertNotIn("Я — Qwen", backend.messages[0].content)


if __name__ == "__main__":
    unittest.main()
