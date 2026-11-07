"""Локальные тесты мгновенного отзыва прав на управление ОС."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from leshy.os_control import OSActionResult, OperatingSystemController
from leshy.storage import Storage
from leshy.module_settings import set_module_enabled
from leshy.os_control_approval import ApprovalOperatingSystemController

class OperatingSystemSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.storage = Storage(root / "db.sqlite3")
        self.app = SimpleNamespace(storage=self.storage, settings=SimpleNamespace(brain_dir=root))
        self.controller = ApprovalOperatingSystemController(self.app)
        self.addCleanup(self.controller._gate.disable)

    def enable(self):
        set_module_enabled(self.storage, "os-control", True)
        self.controller.sync_enabled()

    def test_disabled_by_default_blocks_direct_executor(self):
        self.assertFalse(self.controller.status()["enabled"])
        result = self.controller.execute("system_status", owner_id="web:local")
        self.assertFalse(result.ok)
        self.assertIn("выключено", result.summary)
        with self.assertRaises(PermissionError):
            self.controller.request_delete({"path": str(Path(self.tmp.name) / "x")}, owner_id="web:local")

    def test_toggle_revokes_approvals_and_prevents_late_confirmation(self):
        self.enable()
        path = Path(self.tmp.name) / "must_remain.txt"
        path.write_text("keep", encoding="utf-8")
        request = self.controller.request_delete({"path": str(path)}, owner_id="web:local")
        set_module_enabled(self.storage, "os-control", False)
        stopped = self.controller.sync_enabled()
        self.assertEqual(stopped["approvals_cancelled"], 1)
        self.assertEqual(self.controller.pending(owner_id="web:local"), [])
        self.enable()
        result = self.controller.resolve(request["id"], owner_id="web:local", allow=True)
        self.assertNotEqual(result["status"], "executed")
        self.assertTrue(path.exists())

    def test_disabling_kills_only_tracked_child(self):
        self.enable()
        result = self.controller.execute(
            "start_process",
            {"program": sys.executable, "args": ["-c", "import time;time.sleep(60)"]},
        )
        self.assertTrue(result.ok, result.summary)
        pid = result.data["pid"]
        tracked = self.controller._gate.processes[pid]
        set_module_enabled(self.storage, "os-control", False)
        status = self.controller.sync_enabled()
        self.assertTrue(tracked.poll() is not None)
        self.assertEqual(status["processes_remaining"], 0)
        self.assertFalse(self.controller.execute("system_status").ok)

    def test_reenable_does_not_resume_old_permission(self):
        self.enable()
        token = self.controller._gate.begin()
        self.controller._gate.finish()
        set_module_enabled(self.storage, "os-control", False)
        self.controller.sync_enabled()
        self.enable()
        with self.assertRaises(PermissionError):
            self.controller._gate.check(token)

    def test_parallel_delete_confirmation_is_claimed_once(self):
        self.enable()
        target = Path(self.tmp.name) / "data.txt"
        target.write_text("keep", encoding="utf-8")
        proposal = self.controller.request_delete({"path": str(target)}, owner_id="web:local")
        started, done = threading.Event(), threading.Event()
        runs = []
        answers = {}

        def simulated_delete(*args, **kwargs):
            runs.append("called")
            started.set()
            self.assertTrue(done.wait(4))
            return OSActionResult("delete", True, "Удалено")

        with patch.object(OperatingSystemController, "execute", side_effect=simulated_delete):
            first = threading.Thread(
                target=lambda: answers.update(first=self.controller.resolve(
                    proposal["id"], owner_id="web:local", allow=True,
                )), daemon=True,
            )
            first.start()
            self.assertTrue(started.wait(3))
            second = self.controller.resolve(proposal["id"], owner_id="web:local", allow=True)
            self.assertNotEqual(second["status"], "executed")
            done.set()
            first.join(timeout=3)
        self.assertEqual(runs, ["called"])
        self.assertEqual(answers["first"]["status"], "executed")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_revocation_mid_approval_never_claims_certain_success(self):
        self.enable()
        target = Path(self.tmp.name) / "data.txt"
        target.write_text("keep", encoding="utf-8")
        proposal = self.controller.request_delete({"path": str(target)}, owner_id="web:local")
        started, done = threading.Event(), threading.Event()
        answer = {}
        def simulated_delete(*args, **kwargs):
            started.set()
            self.assertTrue(done.wait(4))
            return OSActionResult("delete", True, "Удалено")
        with patch.object(OperatingSystemController, "execute", side_effect=simulated_delete):
            thread = threading.Thread(
                target=lambda: answer.update(result=self.controller.resolve(
                    proposal["id"], owner_id="web:local", allow=True,
                )), daemon=True,
            )
            thread.start()
            self.assertTrue(started.wait(3))
            set_module_enabled(self.storage, "os-control", False)
            self.controller.sync_enabled()
            done.set()
            thread.join(timeout=3)
        self.assertEqual(answer["result"]["status"], "interrupted")
        self.assertIn("проверьте", answer["result"]["message"])
        self.assertTrue(target.exists())

    def test_non_owner_telegram_cannot_request_system_action(self):
        self.enable()
        self.app.telegram = Mock()
        self.app.telegram._is_owner.side_effect = lambda uid: uid == "100"
        denied = self.controller.execute(
            "system_status", owner_id="telegram:200"
        )
        self.assertFalse(denied.ok)
        self.assertIn("хозяину", denied.summary)
        self.assertTrue(self.controller.execute(
            "system_status", owner_id="telegram:100"
        ).ok)

    def test_telegram_identity_without_bridge_is_denied(self):
        self.enable()
        with self.assertRaises(PermissionError):
            self.controller.request_delete(
                {"path": str(Path(self.tmp.name) / "file")},
                owner_id="telegram:123",
            )

    def test_queued_approvals_are_revoked_when_switch_changes_externally(self):
        self.enable()
        path = Path(self.tmp.name) / "untouched.txt"
        path.write_text("keep", encoding="utf-8")
        proposal = self.controller.request_delete(
            {"path": str(path)}, owner_id="web:local",
        )
        # Моделируем другой процесс, записавший настройку в SQLite
        # минуя HTTP toggle, и имитируем один цикл watchdog.
        set_module_enabled(self.storage, "os-control", False)
        self.controller._gate.disable()
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT status FROM os_action_approvals WHERE id=?",
                (proposal["id"],),
            ).fetchone()
        self.assertEqual(row["status"], "cancelled")
        self.enable()
        self.assertEqual(
            self.controller.resolve(proposal["id"], owner_id="web:local", allow=True)["status"],
            "cancelled",
        )
        self.assertEqual(path.read_text(encoding="utf-8"), "keep")

    def test_restart_with_os_disabled_revokes_old_approval(self):
        self.enable()
        path = Path(self.tmp.name) / "restart.txt"
        path.write_text("keep", encoding="utf-8")
        proposal = self.controller.request_delete(
            {"path": str(path)}, owner_id="web:local",
        )
        set_module_enabled(self.storage, "os-control", False)
        # Имитируем перезапуск с той же SQLite: нет автопринятия старой кнопки.
        another = ApprovalOperatingSystemController(self.app)
        self.addCleanup(another._gate.disable)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT status FROM os_action_approvals WHERE id=?",
                (proposal["id"],),
            ).fetchone()
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(path.read_text(encoding="utf-8"), "keep")

    def test_untracked_opening_is_refused(self):
        self.enable()
        result = self.controller.execute("open_path", {"path": str(Path(self.tmp.name))})
        self.assertFalse(result.ok)

if __name__ == "__main__":
    unittest.main()
