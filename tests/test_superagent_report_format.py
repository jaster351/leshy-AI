from __future__ import annotations

import unittest
from leshy.superagent_report_format import decode_report_json


class ReportParsingTests(unittest.TestCase):
    def test_strict_json(self):
        self.assertEqual(decode_report_json('{"status":"ready","result":"OK"}')["status"], "ready")

    def test_fenced_json(self):
        self.assertEqual(decode_report_json('```json\n{"status":"blocked","result":"Нет доступа"}\n```')["status"], "blocked")

    def test_explanatory_text_and_fence(self):
        result = decode_report_json('Результат проверки:\n```json\n{"status":"waiting","question":"Дайте доступ"}\n```\nЭто всё.')
        self.assertEqual(result["status"], "waiting")

    def test_explanatory_text_and_braced_object(self):
        self.assertEqual(decode_report_json('Итог: {"status":"blocked","result":"нет логов"}'), {"status":"blocked","result":"нет логов"})

    def test_plain_text_rejected(self):
        self.assertIsNone(decode_report_json('Я не могу прочитать логи.'))

    def test_broken_json_rejected(self):
        self.assertIsNone(decode_report_json('{"status":ready,}'))

    def test_multiple_objects_rejected(self):
        self.assertIsNone(decode_report_json('{"status":"ready"} {"status":"failed"}'))
        self.assertIsNone(decode_report_json('```json\n{}\n```\n```json\n{}\n```'))

    def test_too_long_rejected(self):
        self.assertIsNone(decode_report_json('{"status":"ready"}' + ('X' * 33000)))

    def test_tool_json_is_only_data(self):
        self.assertEqual(decode_report_json('{"action":"tool","tool":"shell.exec"}')["action"], 'tool')


if __name__ == '__main__':
    unittest.main()
