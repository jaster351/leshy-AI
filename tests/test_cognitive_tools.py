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

    def test_local_runtime_platform_has_verified_scope(self):
        with patch("leshy.cognitive_tools.platform.system", return_value="Windows"), \
             patch("leshy.cognitive_tools.platform.release", return_value="11"), \
             patch("leshy.cognitive_tools.platform.machine", return_value="AMD64"):
            result = self.executor.execute("runtime.platform", {}, ticket_id="SA-test")
        self.assertEqual(result["result"]["scope"], "leshy_host")
        self.assertEqual(result["result"]["system"], "Windows")
        self.assertNotIn("hostname", result["result"])
        self.assertTrue(self.executor.receipts.verify(
            result["receipt_id"], tool="runtime.platform")["verified"])
        with self.assertRaises(ValueError):
            self.executor.execute("runtime.platform", {"host": "remote"}, ticket_id="SA-test")

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

    def test_remote_os_probe_requires_explicit_permission(self):
        service = self.service([
            '{"status":"ready","result":"Сетевой тест не запускался","question":""}'
        ])
        service.storage = Mock()
        with patch("leshy.superagent.module_enabled", return_value=False), \
             patch("leshy.superagent.probe_remote_os") as probe:
            service._execute({
                "id": "SA-test",
                "request_text": "Проверь сервер 10.20.30.40",
                "attempt": 0,
            })
        probe.assert_not_called()

    def test_remote_os_probe_automatically_uses_user_ip(self):
        service = self.service([
            '{"status":"ready","result":"Проверены сетевые признаки сервера","question":""}'
        ])
        service.storage = Mock()
        service._journal = Mock()
        observation = {
            "host": "10.20.30.40", "os_family": "unknown",
            "confidence": "unknown", "verified": False,
            "tcp": {"22": "open"}, "udp": {"137": "no_response"}, "signals": [],
        }
        with patch("leshy.superagent.module_enabled", return_value=True), \
             patch("leshy.superagent.probe_remote_os", return_value=observation) as probe:
            state, report, _, _ = service._execute({
                "id": "SA-test",
                "request_text": "Проверь сервер 10.20.30.40",
                "attempt": 0,
            })
        self.assertEqual(state, "ready")
        self.assertIn("сетевые", report)
        probe.assert_called_once_with("10.20.30.40")
        self.assertEqual(service._journal.begin.call_args.args[3], "network.os_fingerprint")
        self.assertEqual(service._journal.finish.call_args.kwargs["status"], "success")
        self.assertIn("10.20.30.40", str(service.app.llm.chat.call_args))

    def test_remote_os_probe_rechecks_permission_after_network(self):
        service = self.service([
            '{"status":"ready","result":"ОС установлена","question":""}'
        ])
        service.storage = Mock()
        service._journal = Mock()
        with patch("leshy.superagent.module_enabled", side_effect=[True, False]), \
             patch("leshy.superagent.probe_remote_os", return_value={"os_family": "Linux"}) as probe:
            service._execute({
                "id": "SA-test",
                "request_text": "Проверь сервер 10.20.30.40",
                "attempt": 0,
            })
        probe.assert_called_once()
        self.assertEqual(service._journal.finish.call_args.kwargs["status"], "failed")

    def test_remote_os_ssh_verification_keeps_password_out_of_llm(self):
        service = self.service([
            '{"status":"ready","result":"Проверена ОС","question":""}'
        ])
        service.storage = Mock()
        service._journal = Mock()
        observed = {
            "host": "10.20.30.40", "os_family": "unknown", "confidence": "unknown",
            "verified": False, "tcp": {"22": "open"}, "udp": {"137": "no_response"},
            "signals": [],
        }
        confirmed = {"host": "10.20.30.40", "os_family": "Linux",
                     "verified": True, "method": "ssh:uname"}
        with patch("leshy.superagent.module_enabled", return_value=True), \
             patch("leshy.superagent.probe_remote_os", return_value=observed), \
             patch("leshy.superagent.verify_ssh_os", return_value=confirmed) as verify:
            service._execute({
                "id": "SA-test", "owner_id": "web:a",
                "request_text": "Проверь сервер 10.20.30.40: логин и пароль в файле server.txt",
                "attempt": 0,
            })
        verify.assert_called_once_with(service.storage, "web:a", "10.20.30.40", "server.txt")
        self.assertEqual(service._journal.finish.call_count, 2)
        self.assertNotIn("password", str(service.app.llm.chat.call_args).lower())
        self.assertIn("ssh:uname", str(service.app.llm.chat.call_args))

    def test_install_request_cannot_finish_on_text_only(self):
        service = self.service([
            '{"status":"ready","result":"План найден: скачайте программу с сайта","question":""}',
        ])
        state, report, _, _ = service._execute({
            "id": "SA-test",
            "request_text": "Установить Paint.NET на компьютер, способ определить самостоятельно",
        })
        self.assertEqual(state, "blocked")
        self.assertIn("не подтверждено", report)

    def test_ready_with_question_is_not_completed(self):
        service = self.service([
            '{"status":"ready","result":"Нужно уточнить ОС","question":"Какая операционная система?"}',
        ])
        state, _, question, _ = service._execute({
            "id": "SA-test", "request_text": "Объясни варианты"
        })
        self.assertEqual(state, "waiting")
        self.assertIn("операционная система", question)

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
