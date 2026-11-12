"""SuperAgent must repair malformed final JSON once without faking execution."""
from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock

from leshy.superagent import SuperAgentService


class SuperAgentReportRecoveryTests(unittest.TestCase):
    @staticmethod
    def service(responses):
        service = SuperAgentService.__new__(SuperAgentService)
        service.app = Mock()
        service.app._needs_live_search.return_value = False
        service.app.llm.chat.side_effect = responses
        service._llm_lock = threading.RLock()
        service._inputs = lambda ticket_id: [{"kind": "request", "content": "test"}]
        service._ticket_working = lambda ticket_id: True
        return service

    @staticmethod
    def ticket(request="Изучи последние ошибки в логах"):
        return {"id": "SA-local-report", "request_text": request, "attempt": 0}

    def test_valid_report_stays_single_generation(self):
        service = self.service(['{"status":"blocked","result":"Нет доступа к локальным логам","question":""}'])
        state, report, question, _ = service._execute(self.ticket())
        self.assertEqual(state, "blocked")
        self.assertIn("Нет доступа", report)
        self.assertEqual(question, "")
        self.assertEqual(service.app.llm.chat.call_count, 1)

    def test_explanatory_text_around_json_is_accepted(self):
        service = self.service(['Отчёт:\n```json\n{"status":"blocked","result":"Нет доступа к логам"}\n```'])
        state, report, _, _ = service._execute(self.ticket())
        self.assertEqual(state, "blocked")
        self.assertIn("Нет доступа", report)
        self.assertEqual(service.app.llm.chat.call_count, 1)

    def test_plain_text_is_reformatted_once(self):
        service = self.service([
            "Логи недоступны, уточните путь.",
            '{"status":"blocked","result":"Не имею инструмента чтения логов","question":""}',
        ])
        state, report, _, _ = service._execute(self.ticket())
        self.assertEqual(state, "blocked")
        self.assertIn("инструмента чтения", report)
        self.assertEqual(service.app.llm.chat.call_count, 2)
        repair_prompt = service.app.llm.chat.call_args_list[1].args[0][-1].content
        self.assertIn("ТОЛЬКО формат", repair_prompt)

    def test_second_malformed_answer_does_not_claim_success_or_loop(self):
        service = self.service(["not JSON", "still not JSON"])
        state, report, _, _ = service._execute(self.ticket())
        self.assertEqual(state, "blocked")
        self.assertIn("дважды", report)
        self.assertEqual(service.app.llm.chat.call_count, 2)

    def test_repaired_tool_request_is_never_executed(self):
        service = self.service([
            "invalid",
            '{"action":"tool","tool":"shell.exec","arguments":{"command":"whoami"}}',
        ])
        state, report, _, _ = service._execute(self.ticket())
        self.assertEqual(state, "blocked")
        self.assertIn("дважды", report)
        self.assertEqual(service.app.llm.chat.call_count, 2)

    def test_reformat_cannot_bypass_mutation_receipts(self):
        service = self.service([
            "Программа установлена",
            '{"status":"ready","result":"Я установил программу","question":""}',
        ])
        state, report, _, _ = service._execute(self.ticket("Установи программу"))
        self.assertEqual(state, "blocked")
        self.assertIn("не подтверждено", report)

    def test_stop_before_repair_cancels_extra_generation(self):
        service = self.service(["not JSON"])
        checks = iter([True, True, False])
        service._ticket_working = lambda ticket_id: next(checks)
        state, report, _, _ = service._execute(self.ticket())
        self.assertEqual(state, "blocked")
        self.assertIn("остановлено", report)
        self.assertEqual(service.app.llm.chat.call_count, 1)


if __name__ == "__main__":
    unittest.main()
