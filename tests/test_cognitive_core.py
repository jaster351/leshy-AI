from __future__ import annotations

import unittest

from leshy.cognitive_core import ExecutionEvidence, assess_completion, requires_external_action


class CognitiveCoreEvidenceTests(unittest.TestCase):
    def test_external_operations_require_executor_evidence(self):
        for task in (
            "Установи программу на сервер",
            "Установить на компьютер Paint.NET, способы достижения цели определить самостоятельно",
            "Поставить на компьютер программу",
            "Влей исправление в main",
            "Создай файл конфигурации",
            "Исправь ошибки в коде Лешего",
            "Объясни вариант и затем опубликуй проект",
        ):
            with self.subTest(task=task):
                self.assertTrue(requires_external_action(task))
                decision = assess_completion(task, "ready", "Всё сделано")
                self.assertEqual(decision.state, "blocked")
                self.assertEqual(decision.reason, "missing_mutation_receipt")

    def test_read_only_answer_not_blocked(self):
        for task in ("Объясни, как установить Linux", "Напиши инструкцию", "Составь обзор возможностей"):
            with self.subTest(task=task):
                self.assertFalse(requires_external_action(task))
                self.assertEqual(assess_completion(task, "ready", "Текст ответа").state, "ready")

    def test_only_mutation_receipt_confirms_execution(self):
        task = "Опубликуй программу"
        for proof in (
            (),
            (ExecutionEvidence("1", "research", "success"),),
            (ExecutionEvidence("1", "mutation", "error"),),
            (ExecutionEvidence("", "mutation", "success"),),
        ):
            self.assertEqual(assess_completion(task, "ready", "Готово", evidence=proof).state, "blocked")
        accepted = assess_completion(
            task, "ready", "Изменение применено",
            evidence=(ExecutionEvidence("executor-123", "mutation", "success"),),
        )
        self.assertEqual(accepted.state, "ready")
        self.assertEqual(accepted.reason, "mutation_receipt")

    def test_failed_state_preserved(self):
        result = assess_completion("Установи программу", "failed", "Ошибка сети")
        self.assertEqual((result.state, result.report), ("failed", "Ошибка сети"))


if __name__ == "__main__":
    unittest.main()
