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
