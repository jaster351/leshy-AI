from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

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

    def test_idle_session_listing_never_calls_llm_or_changes_old_title(self):
        with tempfile.TemporaryDirectory() as temp:
            app = self.make_app(Path(temp))
            dashboard = DashboardService(app)
            app.llm.chat = Mock(side_effect=AssertionError("LLM должен оставаться свободным"))
            with app.storage.connection() as conn:
                now = "2026-11-07T10:00:00+00:00"
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    ("old-chat", "web:local", "Важный старый заголовок", "auto", now, now),
                )
                conn.execute(
                    "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                    ("old-chat", "user", "Про старый проект", now),
                )
            for _ in range(3):
                listed = dashboard.sessions(owner_id="web:local")
                self.assertEqual(listed[0]["title"], "Важный старый заголовок")
            app.llm.chat.assert_not_called()
            with app.storage.connection() as conn:
                row = conn.execute(
                    "SELECT title,title_source FROM sessions WHERE id='old-chat'"
                ).fetchone()
                self.assertEqual(row["title_source"], "auto")

    def test_manual_title_generation_is_deterministic_without_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            app = self.make_app(Path(temp))
            dashboard = DashboardService(app)
            app.llm.chat = Mock(side_effect=AssertionError("LLM не требуется"))
            now = "2026-11-07T10:00:00+00:00"
            with app.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    ("new-chat", "web:local", "Новый диалог", "auto", now, now),
                )
                conn.execute(
                    "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                    ("new-chat", "user", "Настройка мониторинга на Linux", now),
                )
            dashboard._auto_title_one("web:local", "new-chat")
            with app.storage.connection() as conn:
                title = conn.execute("SELECT title,title_source FROM sessions WHERE id='new-chat'").fetchone()
            self.assertIn("Настройка мониторинга", title["title"])
            self.assertEqual(title["title_source"], "auto-local")
            app.llm.chat.assert_not_called()

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
