from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.session_admin import SessionAdminService
from leshy.storage import Storage, utcnow
from leshy import web_server_impl7


ROOT = Path(__file__).resolve().parents[1]


class SidebarSessionActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "leshy.db")
        self.admin = SessionAdminService(self.storage)
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("s1", "web:local", "Первый чат", "user", now, now),
            )
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                ("s1", "user", "Привет", now),
            )
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                ("s1", "assistant", "Здравствуйте", now),
            )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_existing_metadata_actions_are_persistent(self) -> None:
        updated = self.admin.update("web:local", "s1", title="Новое имя", pinned=True, locked=True)
        self.assertEqual(updated["title"], "Новое имя")
        self.assertEqual(int(updated["pinned"]), 1)
        self.assertEqual(int(updated["locked"]), 1)
        listed = self.admin.list("web:local")
        self.assertEqual(listed[0]["id"], "s1")
        self.assertEqual(int(listed[0]["pinned"]), 1)

    def test_projects_are_persistent_and_returned_with_session(self) -> None:
        project = self.admin.create_project("web:local", "Работа")
        moved = self.admin.move_to_project("web:local", "s1", project["id"])
        self.assertEqual(moved["project_id"], project["id"])
        self.assertEqual(moved["project_name"], "Работа")
        projects = self.admin.projects("web:local")
        self.assertEqual(projects[0]["chat_count"], 1)
        removed = self.admin.move_to_project("web:local", "s1", None)
        self.assertIsNone(removed["project_id"])

    def test_share_is_read_only_snapshot_and_reusable_until_revoked(self) -> None:
        first = self.admin.create_share("web:local", "s1")
        second = self.admin.create_share("web:local", "s1")
        self.assertEqual(first["token"], second["token"])
        shared = self.admin.resolve_share(first["token"])
        self.assertEqual(shared["session"]["title"], "Первый чат")
        self.assertEqual([m["role"] for m in shared["messages"]], ["user", "assistant"])
        self.admin.revoke_share("web:local", "s1")
        with self.assertRaises(ValueError):
            self.admin.resolve_share(first["token"])

    def test_archive_removes_session_from_recent_list(self) -> None:
        self.admin.update("web:local", "s1", archived=True)
        self.assertEqual(self.admin.list("web:local", archived=False), [])
        archived = self.admin.list("web:local", archived=True)
        self.assertEqual(archived[0]["id"], "s1")

    def test_sidebar_module_does_not_take_over_chat_transport(self) -> None:
        source = (ROOT / "web" / "sidebar-session-actions.js").read_text(encoding="utf-8")
        self.assertNotIn("window.sendFrom=", source)
        self.assertNotIn("/v1/chat/stream", source)
        for label in (
            "Переименовать",
            "Закрепить",
            "Заблокировать чат",
            "Поделиться",
            "Архивировать",
            "Удалить",
            "Переместить в проект",
            "Закрепленные",
            "Проекты",
            "Недавние",
        ):
            self.assertIn(label, source)

    def test_runtime_adds_only_sidebar_module_to_reference_shell(self) -> None:
        original = web_server_impl7.shell._runtime_html
        try:
            web_server_impl7.shell._runtime_html = lambda _settings: b"<html><body><script src=\"/chat-multitask.js\"></script></body></html>"
            rendered = web_server_impl7._runtime_html(object()).decode("utf-8")
        finally:
            web_server_impl7.shell._runtime_html = original
        self.assertIn('/chat-multitask.js', rendered)
        self.assertIn('/sidebar-session-actions.js', rendered)
        self.assertNotIn('/chat-controller-canonical.js', rendered)
        self.assertNotIn('/chat-stall-watchdog.js', rendered)


if __name__ == "__main__":
    unittest.main()
