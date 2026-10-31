from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.app import LeshyApp
from leshy.config import Settings
from leshy.dashboard import DashboardService


class DashboardTests(unittest.TestCase):
    def make_app(self, root: Path) -> LeshyApp:
        brain = root / "LeshyBrain"
        settings = Settings(
            install_root=root,
            brain_dir=brain,
            data_dir=brain / "data",
            database_path=brain / "data" / "leshy.sqlite3",
            native_models_dir=brain / "models",
            workspace_root=brain / "workspace",
            llm_backend="echo",
            embeddings_enabled=False,
        )
        return LeshyApp(settings)

    def test_task_lifecycle_and_overview(self):
        with tempfile.TemporaryDirectory() as temp:
            app = self.make_app(Path(temp))
            dashboard = DashboardService(app)
            task = dashboard.create_task(owner_id="web:local", title="Проверить Лешего", priority=5)
            self.assertEqual(task["status"], "open")
            self.assertEqual(len(dashboard.tasks("web:local")), 1)
            updated = dashboard.update_task(owner_id="web:local", task_id=task["id"], status="done")
            self.assertEqual(updated["status"], "done")
            overview = dashboard.overview("web:local")
            self.assertEqual(overview["tasks"].get("done"), 1)

    def test_dashboard_owner_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            app = self.make_app(Path(temp))
            dashboard = DashboardService(app)
            dashboard.create_task(owner_id="one", title="one")
            self.assertEqual(len(dashboard.tasks("one")), 1)
            self.assertEqual(dashboard.tasks("two"), [])

    def test_capabilities_are_real_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            app = self.make_app(Path(temp))
            items = DashboardService(app).capabilities()
            ids = {item["id"] for item in items}
            self.assertTrue({"chat", "search", "memory", "learning", "telegram", "tasks", "logs"}.issubset(ids))


if __name__ == "__main__":
    unittest.main()
