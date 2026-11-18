"""Regression for long user tasks being silently clipped in the middle."""
from __future__ import annotations

import unittest

from leshy.context_budget import apply_context_budget, estimate_tokens
from leshy.llm import ChatMessage
from leshy.long_instruction_context import structured_task_outline


def _long_task() -> str:
    sections = [
        "# ЛЕШИЙ — ЗАДАНИЕ ПО KNOWLEDGE",
        "Главная цель — пополняй проверенные знания, не придумывай результаты.",
    ]
    for number in range(1, 15):
        sections.append(
            f"## {number}. Раздел о развитии знаний номер {number}"
        )
        sections.append(
            f"Обязательное действие номер {number}: проверь реальные данные, "
            "не используй случайные слова как триггеры. " * 40
        )
    sections.append(
        "Начни с диагностики собственных знаний и одного реально "
        "проверенного цикла их пополнения. Затем используй автономный "
        "механизм для продолжения работы."
    )
    return "\n\n".join(sections)


class LongInstructionContextTests(unittest.TestCase):
    def test_outline_preserves_structural_coverage_and_end(self):
        text = _long_task()
        note = structured_task_outline(text, max_chars=2500)
        self.assertIsNotNone(note)
        self.assertLessEqual(len(note), 2500)
        self.assertIn("а НЕ при приёме сообщения", note)
        self.assertIn("не полный текст задания", note.lower())
        self.assertIn("Раздел о развитии знаний номер 1", note)
        self.assertIn("Раздел о развитии знаний номер 14", note)
        self.assertIn("автономный механизм для продолжения работы", note)
        self.assertIn("Не продолжай и не дописывай текст задания", note)

    def test_small_context_keeps_intent_without_false_network_cutoff(self):
        original = _long_task()
        incoming = [
            ChatMessage("system", "Ты Леший. Выполняй задачи по смыслу."),
            ChatMessage("user", original),
        ]
        limited = apply_context_budget(
            incoming, context_size=4096, reserve_tokens=1024,
        )
        self.assertLessEqual(limited.tokens_after, limited.budget)
        self.assertGreater(limited.truncated_messages, 0)
        user = next(m.content for m in reversed(limited.messages) if m.role == "user")
        self.assertIn("СЖАТО ПРИ ФОРМИРОВАНИИ КОНТЕКСТА МОДЕЛИ", user)
        self.assertIn("Раздел о развитии знаний номер 14", user)
        self.assertIn("Начни с диагностики", user)

    def test_small_request_stays_unchanged(self):
        original = "Проверь Knowledge, не обращаясь к интернету."
        result = apply_context_budget(
            [ChatMessage("system", "Ты Леший."), ChatMessage("user", original)],
            context_size=4096,
        )
        self.assertEqual(result.messages[-1].content, original)

    def test_unstructured_dump_not_presented_as_full_digest(self):
        self.assertIsNone(structured_task_outline("большой лог без разделов " * 400))


if __name__ == "__main__":
    unittest.main()
