"""Regression checks for the Windows tray's shutdown contract (no GUI required)."""
from __future__ import annotations

import threading

from leshy import desktop_tray


class _FakeServer:
    def __init__(self):
        self.shutdown_called = threading.Event()

    def shutdown(self):
        self.shutdown_called.set()


def test_tray_requests_graceful_shutdown_from_other_thread():
    fake = _FakeServer()
    desktop_tray._STOP_REQUESTED.clear()
    desktop_tray.register_server(fake)
    try:
        desktop_tray.request_shutdown()
        assert fake.shutdown_called.wait(timeout=2)
    finally:
        desktop_tray.unregister_server(fake)
        desktop_tray._STOP_REQUESTED.clear()


def test_manual_restart_clears_persistent_tray_stop(tmp_path):
    brain = tmp_path / "LeshyBrain"
    brain.mkdir()
    marker = desktop_tray.pause_marker(brain)
    marker.write_text("exit", encoding="utf-8")
    assert marker.is_file()
    desktop_tray.resume_manually(brain)
    assert not marker.exists()


def test_monitor_launch_uses_separate_console_and_actual_brain(tmp_path, monkeypatch):
    brain = tmp_path / "dist" / "Leshy" / "LeshyBrain"
    brain.mkdir(parents=True)
    script = tmp_path / "windows" / "Leshy-Monitor.ps1"
    script.parent.mkdir()
    script.write_text("# monitor", encoding="utf-8")
    monkeypatch.setattr(desktop_tray, "monitor_script_path", lambda _: script)
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(desktop_tray.subprocess, "Popen", fake_popen)
    desktop_tray.launch_monitor(brain)
    args, kwargs = calls[0]
    assert args[args.index("-File") + 1] == str(script)
    assert args[args.index("-BrainRoot") + 1] == str(brain.resolve())
    assert args[args.index("-Repository") + 1] == str(tmp_path.resolve())
    assert kwargs["shell"] is False
    assert kwargs["creationflags"] == 0x10
    assert kwargs["cwd"] == str(brain.resolve().parent)
