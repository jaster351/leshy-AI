"""Локальные тесты мгновенного отзыва прав на управление ОС."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
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

    def test_untracked_opening_is_refused(self):
        self.enable()
        result = self.controller.execute("open_path", {"path": str(Path(self.tmp.name))})
        self.assertFalse(result.ok)

if __name__ == "__main__":
    unittest.main()
