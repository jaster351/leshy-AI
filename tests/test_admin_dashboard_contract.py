"""UI integration contract: keep chat intact while adding administrative pages."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AdminDashboardContractTests(unittest.TestCase):
    def test_admin_asset_is_loaded_before_superagent(self):
        server = (ROOT / "src/leshy/web_server_impl14.py").read_text(encoding="utf-8")
        self.assertIn('"/admin-dashboard.js"', server)
        self.assertIn('self._send_asset("admin-dashboard.js"', server)
        self.assertLess(
            server.index("'<script src=\"/admin-dashboard.js\"></script>'"),
            server.index("'<script src=\"/superagent-dashboard.js\"></script>'"),
        )

    def test_superagent_has_dedicated_route_with_existing_handlers(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        panel = (ROOT / "web/superagent-dashboard.js").read_text(encoding="utf-8")
        ui = (ROOT / "web/admin-dashboard.js").read_text(encoding="utf-8")
        self.assertIn("superagent:['SuperAgent'", html)
        self.assertIn("pane-superagent", ui)
        self.assertIn("$('pane-superagent') || $('pane-tasks')", panel)
        self.assertIn("'/v1/superagent/tasks", panel)
        self.assertIn("Отправить ответ и продолжить", panel)

    def test_dashboard_displays_only_real_or_unavailable_values(self):
        ui = (ROOT / "web/admin-dashboard.js").read_text(encoding="utf-8")
        for endpoint in (
            "/v1/overview", "/v1/hardware",
            "/v1/cluster/diagnostics", "/v1/swarm/status",
        ):
            self.assertIn(endpoint, ui)
        self.assertIn("Нет данных", ui)
        self.assertIn("Нет подтверждённых координат", ui)
        self.assertIn("Текущая, токен/с", ui)
        self.assertIn("Максимальная, токен/с", ui)
        self.assertNotIn("356,8 TFLOPS", ui)
        self.assertNotIn("12 Активных узлов", ui)

    def test_new_ui_does_not_replace_main_chat(self):
        ui = (ROOT / "web/admin-dashboard.js").read_text(encoding="utf-8")
        self.assertNotIn("pane-chat", ui)
        self.assertNotIn("chatInput", ui)
        self.assertNotIn("chatSend", ui)
        self.assertNotIn("innerHTML =", ui)


if __name__ == "__main__":
    unittest.main()
