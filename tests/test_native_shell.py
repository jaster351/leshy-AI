"""OS shell selection and command lifecycle checks (no real command execution)."""
from __future__ import annotations

import hashlib
import types
import unittest
from unittest import mock

from leshy.native_shell import NativeShell, resolve_native_shell
from leshy.os_control import OperatingSystemController, OSActionResult
from leshy.os_control_approval import ApprovalOperatingSystemController


class NativeShellSelectionTests(unittest.TestCase):
    def test_windows_prefers_installed_powershell_or_cmd(self):
        spec = resolve_native_shell(
            platform_name="nt", which=lambda executable: {
                "pwsh.exe": "C:/Program Files/PowerShell/7/pwsh.exe",
                "powershell.exe": "C:/Windows/System32/PowerShell.exe",
            }.get(executable))
        self.assertEqual(spec.kind, "powershell")
        self.assertEqual(spec.argv("Get-Date"), [
            "C:/Program Files/PowerShell/7/pwsh.exe",
            "-NoProfile", "-NonInteractive", "-Command", "Get-Date"
        ])
        fallback = resolve_native_shell(
            platform_name="windows", which=lambda name: "C:/Windows/System32/cmd.exe"
            if name == "cmd.exe" else None,
        )
        self.assertEqual(fallback.kind, "cmd")
        self.assertEqual(fallback.argv("ver")[-4:], ["/D", "/S", "/C", "ver"])

    def test_unix_prefers_bash_then_sh(self):
        linux = resolve_native_shell(
            platform_name="linux", which=lambda exe: "/usr/bin/" + exe)
        self.assertEqual(linux.kind, "bash")
        self.assertEqual(linux.argv("pwd"), [
            "/usr/bin/bash", "--noprofile", "--norc", "-c", "pwd",
        ])
        unix = resolve_native_shell(
            platform_name="darwin", which=lambda exe: "/bin/sh" if exe == "sh" else None)
        self.assertEqual(unix.kind, "sh")
        self.assertEqual(unix.argv("pwd"), ["/bin/sh", "-c", "pwd"])

    def test_fails_closed_without_shell_or_invalid_command(self):
        with self.assertRaises(RuntimeError):
            resolve_native_shell(platform_name="linux", which=lambda _: None)
        with self.assertRaises(RuntimeError):
            resolve_native_shell(platform_name="totally-new-os")
        shell = NativeShell("bash", "/bin/bash", ("-c",))
        for command in ("", "  ", "\x00", "x" * 4097):
            with self.subTest(command=command[:12]):
                with self.assertRaises(ValueError):
                    shell.argv(command)


class FakeProcess:
    returncode = 0

    def communicate(self, timeout):
        return b"native shell output", b""

    def poll(self):
        return self.returncode


class FakeGate:
    def __init__(self):
        self.calls = []
        self.process = FakeProcess()

    def check(self, generation=None):
        self.calls.append(("check", generation))

    def launch(self, argv, *, capture=False):
        self.calls.append(("launch", argv, capture))
        return self.process

    def unregister(self, proc):
        self.calls.append(("unregister", proc))

    def terminate(self, proc):
        self.calls.append(("terminate", proc))


class NativeShellControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = object.__new__(OperatingSystemController)
        self.controller._gate = FakeGate()
        self.controller._local = types.SimpleNamespace(generation=123)

    def test_shell_reuses_existing_gate_and_returns_output(self):
        spec = NativeShell("sh", "/bin/sh", ("-c",))
        with mock.patch("leshy.os_control.resolve_native_shell", return_value=spec):
            kind, value = self.controller._native_shell("printf 'ok'", timeout=4)
        self.assertEqual(kind, "sh")
        self.assertIn("native shell output", value)
        self.assertIn(("launch", ["/bin/sh", "-c", "printf 'ok'"], True),
                      self.controller._gate.calls)
        self.assertTrue(any(name == "unregister" for name, *_ in self.controller._gate.calls))

    def test_failed_shell_does_not_write_stdout_into_exception(self):
        self.controller._gate.process.returncode = 7
        spec = NativeShell("sh", "/bin/sh", ("-c",))
        with mock.patch("leshy.os_control.resolve_native_shell", return_value=spec):
            with self.assertRaisesRegex(RuntimeError, "кодом 7") as failure:
                self.controller._native_shell("exit 7", timeout=4)
        self.assertNotIn("native shell output", str(failure.exception))

    def test_direct_run_maps_to_current_os(self):
        command = OperatingSystemController._direct_command("/os run echo hello")
        self.assertEqual(command, ("native_shell", {"command": "echo hello"}))
        shell = OperatingSystemController._direct_command("/os shell pwd")
        self.assertEqual(shell, ("native_shell", {"command": "pwd"}))

    def test_command_content_not_recorded_in_audit(self):
        controller = object.__new__(OperatingSystemController)
        calls = []
        controller.storage = types.SimpleNamespace(
            audit=lambda kind, payload: calls.append((kind, payload)))
        controller._last_action = None
        secret = "echo super-sensitive-token"
        controller._audit("native_shell", {"command":secret},
                          OSActionResult("native_shell", True, "Команда выполнена"),
                          owner_id="owner-test")
        output = calls[0][1]
        self.assertEqual(output["arguments"]["command"]["sha256"],
                         hashlib.sha256(secret.encode()).hexdigest())
        self.assertNotIn(secret, str(output))

    def test_shell_delete_cannot_bypass_confirmation(self):
        controller = object.__new__(ApprovalOperatingSystemController)
        controller._authorize_owner = lambda owner: None
        controller.enabled = lambda: True
        with mock.patch.object(OperatingSystemController, "execute") as dispatch:
            result = controller.execute(
                "native_shell", {"command": "rm -rf /home/user/test"},
                owner_id="owner-test",
            )
        self.assertFalse(result.ok)
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
