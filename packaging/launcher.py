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
    parser.add_argument("--host", default=os.getenv("LESHY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LESHY_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    install_root = _install_root()
    brain_root = (install_root / "LeshyBrain").resolve()

    os.environ["LESHY_INSTALL_ROOT"] = str(install_root)
    os.environ["LESHY_BRAIN_DIR"] = str(brain_root)
    os.environ["LESHY_HOST"] = args.host
    os.environ["LESHY_PORT"] = str(args.port)
    os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    from leshy.diagnostic_publisher import DiagnosticPublisher
    from leshy.web_server import serve

    publisher = DiagnosticPublisher(brain_root)
    publisher.start()
    try:
        if not args.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()
        serve(args.host, args.port)
    finally:
        publisher.stop()


if __name__ == "__main__":
    main()
