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
from typing import Any, Callable

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


def monitor_script_path(brain_root: Path, *, console: bool = False) -> Path:
    """Prefer the monitor bundled with the running application."""
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    filename = "Leshy-Monitor.ps1" if console else "Leshy-Monitor-GUI.ps1"
    bundled = bundle / filename
    if bundled.is_file():
        return bundled
    source = bundle / "windows" / filename
    if source.is_file():
        return source
    raise FileNotFoundError("Не найден штатный " + filename)


def launch_monitor(brain_root: Path, *, console: bool = False):
    """Open an independent WPF window, or the explicit technical console."""
    brain = Path(brain_root).resolve()
    script = monitor_script_path(brain, console=console)
    root = brain.parent
    if len(brain.parents) >= 3 and brain == brain.parents[2] / "dist" / "Leshy" / "LeshyBrain":
        root = brain.parents[2]
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    exe = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    args = [str(exe), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if not console:
        args.extend(["-STA", "-WindowStyle", "Hidden"])
    args.extend(["-File", str(script), "-Repository", str(root), "-BrainRoot", str(brain)])
    return subprocess.Popen(
        args, cwd=str(brain.parent), shell=False,
        creationflags=(getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10) if console
                       else getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)),
    )


def start_tray(
    brain_root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_web: Callable[[], bool] | None = None,
):
    """Запускает трей Windows и использует одно управляемое Web-окно."""
    if os.name != "nt":
        return None

    from PIL import Image
    import pystray

    from leshy.update_feedback import TrayUpdateFeedback

    url = f"http://127.0.0.1:{int(port)}/"
    image_file = _icon_path()
    image = Image.open(image_file).convert("RGBA")
    holder = {}

    def notify_updates(message: str) -> None:
        icon = holder.get("icon")
        if icon is None:
            return
        try:
            icon.notify(message, "Леший: проверка обновлений")
        except (AttributeError, NotImplementedError, OSError):
            _LOG.exception("Уведомления Windows недоступны, статус остаётся в меню трея")

    def update_menu() -> None:
        icon = holder.get("icon")
        if icon is not None:
            icon.update_menu()

    # dist/Leshy/LeshyBrain -> repository root.
    # A standalone installer outside the Git repository cannot invoke this
    # particular Git Task Scheduler action and reports that clearly.
    repo_root = Path(brain_root).resolve().parents[2]
    feedback = TrayUpdateFeedback(
        repo_root,
        notify=notify_updates,
        changed=update_menu,
    )

    def open_web_item(icon=None, item=None):
        if open_web is not None:
            # Открыть или переиспользовать только окно этого экземпляра.
            if not open_web():
                _LOG.warning("Не удалось открыть управляемое окно Лешего")
        else:
            # Сохранено для вызовов start_tray вне штатного Windows launcher.
            webbrowser.open(url)

    def check_updates(icon=None, item=None):
        # No more hidden fire-and-forget task. A background monitor reports
        # checking, already current, building, installed or a real failure.
        feedback.start()

    monitor_lock = threading.Lock()
    monitor_processes = {}

    def open_monitor(icon=None, item=None, *, console=False):
        with monitor_lock:
            monitor_process = monitor_processes.get(console)
            if monitor_process is not None and monitor_process.poll() is None:
                try:
                    if icon is not None:
                        icon.notify("Окно монитора уже открыто.", "Леший")
                except (AttributeError, NotImplementedError, OSError):
                    pass
                return
            try:
                monitor_processes[console] = launch_monitor(brain_root, console=console)
            except (OSError, ValueError):
                _LOG.exception("Не удалось запустить монитор Лешего")
                try:
                    if icon is not None:
                        icon.notify("Не удалось открыть монитор. Подробности в журнале.", "Леший")
                except (AttributeError, NotImplementedError, OSError):
                    pass

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
        pystray.MenuItem("Открыть Лешего", open_web_item, default=True),
        pystray.MenuItem("Монитор", open_monitor),
        pystray.MenuItem("Монитор (консоль)", lambda icon, item: open_monitor(icon, item, console=True)),
        pystray.MenuItem("Проверить обновления", check_updates),
        pystray.MenuItem(
            lambda item: "Обновления: " + (
                feedback.message[:92] + "..." if len(feedback.message) > 95
                else feedback.message
            ),
            None,
            enabled=False,
        ),
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
