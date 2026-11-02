from __future__ import annotations

from pathlib import Path
import unittest

from leshy.superagent import parse_superagent_intent


ROOT = Path(__file__).resolve().parents[1]


class SuperAgentContractTests(unittest.TestCase):
    def test_explicit_commands_create_and_manage_tickets(self) -> None:
        created = parse_superagent_intent("/agent изучи вопрос и подготовь вывод")
        self.assertIsNotNone(created)
        self.assertEqual(created.action, "create")
        self.assertIn("подготовь вывод", created.argument)

        status = parse_superagent_intent("/agent status SA-20261102123456789-abcdef")
        self.assertEqual(status.action, "status")
        self.assertTrue(status.ticket_id.startswith("SA-"))

        answer = parse_superagent_intent("/agent answer SA-20261102123456789-abcdef Да, продолжай")
        self.assertEqual(answer.action, "answer")
        self.assertEqual(answer.argument, "Да, продолжай")

    def test_ordinary_chat_is_not_silently_converted_to_autonomous_work(self) -> None:
        self.assertIsNone(parse_superagent_intent("Расскажи, что такое автономный агент"))

    def test_durable_domain_contract_is_persisted(self) -> None:
        source = (ROOT / "src" / "leshy" / "superagent.py").read_text(encoding="utf-8")
        self.assertIn("superagent_tickets", source)
        self.assertIn("superagent_inputs", source)
        self.assertIn("superagent_assignments", source)
        self.assertIn("mandate_json", source)
        self.assertIn("outcome='interrupted'", source)
        for state in ("queued", "working", "waiting", "ready", "blocked", "failed", "stopped"):
            self.assertIn(state, source)

    def test_background_inference_uses_low_priority_scheduler(self) -> None:
        source = (ROOT / "src" / "leshy" / "superagent_scheduler.py").read_text(encoding="utf-8")
        self.assertIn("PRIORITY_BACKGROUND", source)
        self.assertIn("get_inference_scheduler", source)
        self.assertIn("scheduler.slot", source)
        self.assertIn("cancel_event=self._stop", source)

    def test_module_is_visible_in_settings_and_toggle_is_live(self) -> None:
        settings = (ROOT / "src" / "leshy" / "module_settings.py").read_text(encoding="utf-8")
        server = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
        panel = (ROOT / "web" / "module-settings-panel.js").read_text(encoding="utf-8")
        self.assertIn('name="superagent"', settings)
        self.assertIn('title="SuperAgent"', settings)
        self.assertIn("install_superagent_background_scheduler()", server)
        self.assertIn("install_superagent_routing(LeshyApp, app)", server)
        self.assertIn('module_name == "superagent"', server)
        self.assertIn("sync_superagent_runtime_state(app)", server)
        self.assertIn("data-module-toggle", panel)

    def test_global_idempotency_remains_outermost_chat_contract(self) -> None:
        server = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
        install = server[server.index("def _install_runtime_contracts"):server.index("def _runtime_html")]
        self.assertLess(install.index("install_superagent_routing"), install.index("install_global_chat_idempotency"))


if __name__ == "__main__":
    unittest.main()
