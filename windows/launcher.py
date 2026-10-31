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


def _locator_file() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "LeshyAI"
    base.mkdir(parents=True, exist_ok=True)
    return base / "data-root.txt"


def _default_brain() -> Path:
    return _locator_file().parent / "LeshyBrain"


def _resolve_brain(cli_value: str | None) -> Path:
    locator = _locator_file()
    if cli_value:
        chosen = Path(cli_value).expanduser().resolve()
        chosen.mkdir(parents=True, exist_ok=True)
        locator.write_text(str(chosen), encoding="utf-8")
        return chosen
    if locator.is_file():
        value = locator.read_text(encoding="utf-8").strip()
        if value:
            chosen = Path(value).expanduser()
            if not chosen.exists():
                raise SystemExit(
                    "Выбранный каталог данных Лешего недоступен: " + str(chosen) +
                    "\nПодключите диск или запустите Leshy.exe --data-root <путь>."
                )
            return chosen.resolve()
    chosen = _default_brain().resolve()
    chosen.mkdir(parents=True, exist_ok=True)
    locator.write_text(str(chosen), encoding="utf-8")
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(prog="Leshy")
    parser.add_argument("--data-root", help="Каталог LeshyBrain; выбор сохраняется для следующих запусков")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    os.environ["LESHY_INSTALL_ROOT"] = str(_install_root())
    os.environ["LESHY_BRAIN_DIR"] = str(_resolve_brain(args.data_root))
    os.environ.setdefault("LESHY_HOST", args.host)
    os.environ.setdefault("LESHY_PORT", str(args.port))

    from leshy.web_server import serve

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
