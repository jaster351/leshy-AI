"""Windows system tray for the packaged Leshy.exe.

The tray runs independently of the web-server request loop. "Exit" requests
a graceful HTTPServer.shutdown(), preserving LeshyBrain data and suppressing
the scheduled task's automatic relaunch until the user starts Leshy manually.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from typing import Any

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_ACTIVE_SERVER: Any | None = None
_STOP_REQUESTED = threading.Event()


def register_server(server: Any) -> None:
    """Called by the HTTP server after bind, before serve_forever()."""
    global _ACTIVE_SERVER
    with _LOCK:
        _ACTIVE_SERVER = server
        requested = _STOP_REQUESTED.is_set()
    if requested:
        _request_shutdown(server)


def unregister_server(server: Any) -> None:
    global _ACTIVE_SERVER
    with _LOCK:
        if _ACTIVE_SERVER is server:
            _ACTIVE_SERVER = None


def _request_shutdown(server: Any) -> None:
    # BaseServer.shutdown() MUST run in another thread AND only after the server
    # has entered serve_forever(). The tray may be clicked during startup.
    def run():
        started = getattr(server, "_BaseServer__is_shut_down", None)
        if started is not None:
            import time
            while started.is_set():
                time.sleep(0.05)
        server.shutdown()

    threading.Thread(target=run, name="leshy-tray-shutdown", daemon=True).start()


def is_shutdown_requested() -> bool:
    return _STOP_REQUESTED.is_set()


def request_shutdown() -> None:
    _STOP_REQUESTED.set()
    with _LOCK:
        server = _ACTIVE_SERVER
    if server is not None:
        _request_shutdown(server)


def pause_marker(brain_root: Path) -> Path:
    return Path(brain_root) / "manual-stop.flag"


def resume_manually(brain_root: Path) -> None:
    """Manual application starts clear the persistent stop request."""
    pause_marker(brain_root).unlink(missing_ok=True)


def _icon_path() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / "leshy-icon.jpg"


def start_tray(brain_root: Path, *, host: str = "127.0.0.1", port: int = 8765):
    """Start an optional Windows tray. Does not depend on a console window."""
    if os.name != "nt":
        return None

    from PIL import Image
    import pystray

    url = f"http://127.0.0.1:{int(port)}/"
    image_file = _icon_path()
    image = Image.open(image_file).convert("RGBA")
    holder = {}

    def open_web(icon=None, item=None):
        webbrowser.open(url)

    def check_updates(icon=None, item=None):
        # Task Scheduler enforces no overlapping builds.
        exe = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe")
        try:
            subprocess.Popen(
                [exe, "/Run", "/TN", "Leshy AI - Git Build (15 min)"],
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            _LOG.exception("Cannot request scheduled update")

    def exit_leshy(icon, item=None):
        try:
            marker = pause_marker(brain_root)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        except OSError:
            _LOG.exception("Failed to persist manual stop request")
            # Never exit if the scheduler cannot see the stop request.
            return
        icon.stop()
        request_shutdown()

    menu = pystray.Menu(
        pystray.MenuItem("Открыть Лешего", open_web, default=True),
        pystray.MenuItem("Проверить обновления", check_updates),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выйти из Лешего", exit_leshy),
    )
    icon = pystray.Icon("Leshy-AI", image, "Леший AI", menu)
    holder["icon"] = icon

    def run():
        try:
            icon.run()
        except Exception:
            _LOG.exception("Windows tray failed")

    thread = threading.Thread(target=run, name="leshy-windows-tray", daemon=True)
    thread.start()
    return icon
