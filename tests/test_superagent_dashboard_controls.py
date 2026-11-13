"""SuperAgent UI features must remain discoverable and owner scoped."""
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from leshy.superagent_dashboard import execute_action, ticket_details
from test_superagent_dashboard import FakeService


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "web" / "superagent-dashboard.js"
CONTRACT = ROOT / "docs" / "INTERFACE_RULES_CURRENT.md"


class DashboardControlsTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.service.plans.set_auto = Mock(side_effect=lambda ticket_id, owner_id, enabled: {
            "id": ticket_id, "state": "active", "auto_run": enabled
        })
        self.service._wake = Mock()

    def test_owner_can_toggle_auto_and_wake_scheduler(self):
        result = execute_action(self.service, "web:alice",
                                {"action": "plan_auto", "id": "SA-123"})
        self.assertTrue(result["plan"]["auto_run"])
        self.service.plans.set_auto.assert_called_once_with("SA-123", "web:alice", True)
        self.service._wake.set.assert_called_once()
        result = execute_action(self.service, "web:alice",
                                {"action": "plan_manual", "id": "SA-123"})
        self.assertFalse(result["plan"]["auto_run"])
        self.service.plans.set_auto.assert_called_with("SA-123", "web:alice", False)

    def test_unauthorized_user_and_terminal_task_cannot_enable_auto(self):
        with self.assertRaises(PermissionError):
            execute_action(self.service, "web:bob", {"action": "plan_auto", "id": "SA-123"})
        self.assertFalse(self.service.plans.set_auto.called)
        self.service.tasks["SA-123"]["state"] = "stopped"
        with self.assertRaises(ValueError):
            execute_action(self.service, "web:alice", {"action": "plan_auto", "id": "SA-123"})
        self.assertFalse(self.service.plans.set_auto.called)

    def test_explicit_read_action_runs_only_for_owner_and_live_ticket(self):
        expected = {"id": "SA-123", "state": "active", "completed": 2, "errors": 0, "total": 3}
        with patch("leshy.superagent_plan_runner.execute_ready_plan_reads",
                   return_value=expected) as run:
            with self.assertRaises(PermissionError):
                execute_action(self.service, "web:bob", {"action": "plan_run", "id": "SA-123"})
            run.assert_not_called()
            answer = execute_action(self.service, "web:alice", {"action": "plan_run", "id": "SA-123"})
            self.assertEqual(answer["plan"]["completed"], 2)
            run.assert_called_once_with(self.service, "SA-123", "web:alice")
            self.service.tasks["SA-123"]["state"] = "failed"
            with self.assertRaises(ValueError):
                execute_action(self.service, "web:alice", {"action": "plan_run", "id": "SA-123"})
            self.assertEqual(run.call_count, 1)

    def test_details_expose_progress_not_private_output(self):
        self.service.plans.snapshot = Mock(return_value={
            "state": "active", "auto_run": True, "steps": [
                {"id": "step1", "title": "Check version", "state": "done",
                 "tool": "runtime.version", "output": "private raw data"},
                {"id": "step2", "title": "Manual", "state": "queued",
                 "tool": "", "output": "secret"},
            ]})
        detail = ticket_details(self.service, "web:alice", "SA-123")["task"]
        self.assertTrue(detail["plan"]["auto_run"])
        self.assertEqual(detail["plan"]["read_steps_total"], 1)
        self.assertEqual(detail["plan"]["state_counts"]["done"], 1)
        self.assertFalse(any("output" in item for item in detail["plan"]["steps"]))
        with self.assertRaises(PermissionError):
            ticket_details(self.service, "web:bob", "SA-123")

    def test_web_ui_has_controls_instead_of_requiring_cli_or_json(self):
        js = UI.read_text(encoding="utf-8")
        for snippet in (
            "Включить автозапуск проверок", "Выключить автозапуск проверок",
            "Выполнить доступные проверки", "action:'plan_run'",
            "action:'plan_create'", "knowledge.search", "runtime.version",
            "runtime.platform", "Добавьте действия кнопками",
            "expandedTasks.add(task.id)", "expandedTasks.clear()",
            "Автоматически доступны только безопасные проверки",
            "Что искать?", "Проверок выполнено:",
        ):
            self.assertIn(snippet, js)
        self.assertNotIn("Что делают кнопки и команды?", js)

    def test_ui_first_contract_is_project_wide(self):
        doc = CONTRACT.read_text(encoding="utf-8")
        self.assertIn("UI-first", doc)
        self.assertIn("**любой** функции Лешего", doc)
        self.assertIn("Не заставлять запоминать команды", doc)
        self.assertIn("Telegram", doc)


if __name__ == "__main__":
    unittest.main()
