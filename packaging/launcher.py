from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(prog="Leshy")
    parser.add_argument("--host", default=os.getenv("LESHY_HOST", "auto"),
                        help="auto: все интерфейсы IPv4/IPv6; явный адрес ограничивает привязку")
    parser.add_argument("--port", type=int, default=None,
                        help="HTTP-порт: явный параметр, LESHY_PORT, сохранённый порт, затем 8765")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    install_root = _install_root()
    brain_root = (install_root / "LeshyBrain").resolve()

    os.environ["LESHY_INSTALL_ROOT"] = str(install_root)
    os.environ["LESHY_BRAIN_DIR"] = str(brain_root)
    os.environ["LESHY_HOST"] = args.host
    if args.port is not None:
        os.environ["LESHY_PORT"] = str(args.port)
    os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    from leshy.diagnostic_publisher import DiagnosticPublisher
    from leshy.web_server import serve

    # Адрес берётся только из запущенного listener, включая HTTPS-only.
    browser_ui = None
    ready_url = None
    browser_timer = None
    browser_closing = False
    browser_lock = threading.Lock()

    def open_window() -> bool:
        nonlocal browser_ui
        with browser_lock:
            if browser_closing or ready_url is None:
                return False
            if os.name == "nt":
                from leshy.managed_browser import ManagedBrowser
                if browser_ui is None:
                    browser_ui = ManagedBrowser(ready_url)
                return browser_ui.open()
            return webbrowser.open(ready_url)

    def on_listening(target_url: str) -> None:
        nonlocal ready_url, browser_timer
        with browser_lock:
            if browser_closing:
                return
            ready_url = target_url
        if not args.no_browser:
            browser_timer = threading.Timer(0.1, open_window)
            browser_timer.daemon = True
            browser_timer.start()

    publisher = DiagnosticPublisher(brain_root)
    publisher.start()
    try:
        serve(args.host, args.port, on_listening=on_listening)
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
        with browser_lock:
            browser_closing = True
            if browser_ui is not None:
                browser_ui.close()
        publisher.stop()


if __name__ == "__main__":
    main()
