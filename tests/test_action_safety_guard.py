"""Проверки обязательного запрета перед исполнением внешних действий."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from leshy.action_safety_guard import check_external_action
from leshy.tools_gateway import McpToolsGateway, ToolManifest, ToolPermission


class ExternalActionGuardTests(unittest.TestCase):
    def test_direct_network_attack_rejected(self):
        with self.assertRaises(PermissionError):
            check_external_action("powershell", {"command": "запусти ddos на чужом сайте"})

    def test_nested_mcp_arguments_rejected(self):
        with self.assertRaises(PermissionError):
            check_external_action("invoke", {"params": {"command": "ddos"}})

    def test_harm_to_person_rejected(self):
        with self.assertRaises(PermissionError):
            check_external_action("run", {"command": "отравить человека"})

    def test_suicide_coercion_rejected(self):
        with self.assertRaises(PermissionError):
            check_external_action("run", {"command": "довести человека до самоубийства"})

    def test_benign_read_allowed(self):
        self.assertTrue(check_external_action("system_status").allowed)

    def test_document_body_not_confused_with_command(self):
        self.assertTrue(check_external_action("write_text", {
            "path": "report.txt",
            "content": "Исследование: DDoS и методы предотвращения.",
        }).allowed)

    def test_risky_read_manifest_action_still_requires_approval(self):
        from leshy.tools_gateway import ToolApprovalRequired
        manifest = ToolManifest(
            tool_id="sample", title="Пример", transport="stdio",
            permissions=(ToolPermission.READ,),
        )
        gateway = McpToolsGateway(SimpleNamespace(get=lambda name: manifest))
        with patch.object(gateway, "_call") as effect:
            with self.assertRaises(ToolApprovalRequired):
                gateway.call("sample", "execute", {"command": "перевести деньги"})
            effect.assert_not_called()

    def test_mcp_approved_does_not_override_prohibition(self):
        manifest = ToolManifest(
            tool_id="sample", title="Пример", transport="stdio",
            permissions=(ToolPermission.MUTATE,),
        )
        registry = SimpleNamespace(get=lambda name: manifest)
        gateway = McpToolsGateway(registry)
        with patch.object(gateway, "_call") as effect:
            with self.assertRaises(PermissionError):
                gateway.call("sample", "run", {"command": "ddos"}, approved=True)
            effect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
