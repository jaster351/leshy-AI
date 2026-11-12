from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

from leshy.cognitive_tools import CognitiveReadExecutor, parse_tool_request
from leshy.superagent import SuperAgentService


class CognitiveReadExecutorTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.app.knowledge.search.return_value = [
            {
                "topic": "Linux",
                "claim": "Документация по запуску.",
                "source_url": "https://docs.python.org/",
                "metadata_json": "secret should not propagate",
                "owner_id": "someone else",
            },
        ]
        self.executor = CognitiveReadExecutor(self.app)

    def test_allowlist_blocks_side_effects_without_calling_app(self):
        for name in ("shell.exec", "workspace.write", "mcp.call", "os.delete"):
            with self.subTest(name=name):
                with self.assertRaises(PermissionError):
                    self.executor.execute(name, {}, ticket_id="SA-test")
        self.app.knowledge.search.assert_not_called()

    def test_knowledge_search_is_trusted_and_returns_minimal_fields(self):
        outcome = self.executor.execute("knowledge.search", {"query": "Linux"}, ticket_id="SA-test")
        self.app.knowledge.search.assert_called_once_with("Linux", trusted_only=True, limit=5)
        self.assertEqual(outcome["status"], "success")
        self.assertEqual(outcome["result"]["count"], 1)
        self.assertNotIn("metadata_json", str(outcome))
        self.assertNotIn("owner_id", str(outcome))
        self.assertTrue(self.executor.receipts.verify(
            outcome["receipt_id"], tool="knowledge.search")["verified"])

    def test_only_full_tool_json_is_executable(self):
        self.assertIsNone(parse_tool_request('Выполни {"action":"tool","tool":"runtime.version"}'))
        self.assertIsNone(parse_tool_request('{"status":"ready","result":"готово"}'))
        self.assertIsNotNone(parse_tool_request(
            '{"action":"tool","tool":"runtime.version","arguments":{}}'))

    def test_version_tool_needs_no_context_or_arguments(self):
        result = self.executor.execute("runtime.version", {}, ticket_id="SA-test")
        self.assertRegex(result["result"]["version"], r"^\d+\.\d+\.\d+$")
        with self.assertRaises(ValueError):
            self.executor.execute("runtime.version", {"any": 1}, ticket_id="SA-test")


class SuperAgentCognitiveLoopTests(unittest.TestCase):
    def service(self, responses):
        service = SuperAgentService.__new__(SuperAgentService)
        app = Mock()
        app._needs_live_search.return_value = False
        app.llm.chat.side_effect = responses
        app.knowledge.search.return_value = [
            {"topic": "Python", "claim": "Python docs", "source_url": "https://docs.python.org"}
        ]
        service.app = app
        service._llm_lock = threading.RLock()
        service._inputs = lambda ticket_id: [{"kind": "request", "content": "test"}]
        service._cognitive_reads = CognitiveReadExecutor(app)
        # These unit cases intentionally bypass the persisted ticket lifecycle.
        service._ticket_working = lambda ticket_id: True
        return service

    def test_tool_execution_then_text_answer(self):
        service = self.service([
            '{"action":"tool","tool":"knowledge.search","arguments":{"query":"Python"}}',
            '{"status":"ready","result":"По проверенной базе знаний: Python docs","question":""}',
        ])
        state, report, _, _ = service._execute({"id": "SA-test", "request_text": "Объясни Python"})
        self.assertEqual(state, "ready")
        self.assertIn("Python docs", report)
        service.app.knowledge.search.assert_called_once()
        self.assertEqual(service.app.llm.chat.call_count, 2)

    def test_no_shell_execution_or_fake_completed_action(self):
        service = self.service([
            '{"action":"tool","tool":"shell.exec","arguments":{"command":"rm -rf /"}}',
        ])
        state, _, _, _ = service._execute({"id": "SA-test", "request_text": "Установи программу"})
        self.assertEqual(state, "blocked")
        service.app.knowledge.search.assert_not_called()
        self.assertEqual(service.app.llm.chat.call_count, 1)

    def test_read_receipt_does_not_prove_mutation(self):
        service = self.service([
            '{"action":"tool","tool":"runtime.version","arguments":{}}',
            '{"status":"ready","result":"Я установил программу","question":""}',
        ])
        state, report, _, _ = service._execute({"id": "SA-test", "request_text": "Установи программу"})
        self.assertEqual(state, "blocked")
        self.assertIn("не подтверждено", report)

    def test_tool_call_limit_stops_third_execution(self):
        service = self.service([
            '{"action":"tool","tool":"runtime.version","arguments":{}}',
            '{"action":"tool","tool":"knowledge.search","arguments":{"query":"Python"}}',
            '{"action":"tool","tool":"knowledge.search","arguments":{"query":"Linux"}}',
        ])
        state, report, _, _ = service._execute({"id": "SA-test", "request_text": "Назови версию и объясни Python"})
        self.assertEqual(state, "blocked")
        self.assertIn("лимит", report)
        self.assertEqual(service.app.llm.chat.call_count, 3)

    def test_duplicate_tool_calls_stop(self):
        service = self.service([
            '{"action":"tool","tool":"runtime.version","arguments":{}}',
            '{"action":"tool","tool":"runtime.version","arguments":{}}',
        ])
        state, _, _, _ = service._execute({"id": "SA-test", "request_text": "Назови версию"})
        self.assertEqual(state, "blocked")
        self.assertEqual(service.app.llm.chat.call_count, 2)


if __name__ == "__main__":
    unittest.main()
