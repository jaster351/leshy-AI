"""Regression checks: quoted document names never hijack normal dialogue."""
from __future__ import annotations

import unittest

from leshy.constitution_runtime import is_federation_constitution_question


class ConstitutionIntentRoutingTests(unittest.TestCase):
    def test_explicit_document_read_requests(self):
        for message in (
            "Покажи полный текст Конституции Федерации Леших",
            "Леший, прочитай статью 12 Конституции Федерации Леших",
            "Что сказано в статье 18 Конституции Федерации Леших?",
            "Сколько статей в Конституции Федерации Леших?",
            "Покажи нашу конституцию",
        ):
            with self.subTest(message=message):
                self.assertTrue(is_federation_constitution_question(message))

    def test_mentions_inside_other_tasks_do_not_intercept(self):
        for message in (
            "При разработке учитывай Конституцию Федерации Леших и мои инструкции.",
            "Леший, развивай Knowledge и не забывай Конституцию Федерации Леших.",
            "Объясни, почему упоминание Конституции Федерации Леших ломает ответы.",
            "Убери триггер-слова «Конституция Федерации Леших» и «создатель» из маршрутизатора.",
            "Покажи список ошибок, из-за которых бот вспоминает Конституцию Федерации Леших.",
            "Кто создатель Лешего?",
            "Покажи Конституцию РФ",
            "Кто создатель Федерации Леших по Конституции?",
            "Сохрани Knowledge, проверь кластер и учитывай Конституцию Федерации Леших.\nЗатем дай отчёт.",
        ):
            with self.subTest(message=message):
                self.assertFalse(is_federation_constitution_question(message))


if __name__ == "__main__":
    unittest.main()
