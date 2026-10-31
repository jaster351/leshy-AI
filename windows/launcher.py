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


def _brain_root() -> Path:
    """Единый каталог данных, всегда относительно Leshy.exe/корня запуска."""
    return (_install_root() / "LeshyBrain").resolve()


def _legacy_brain() -> Path:
    """Старое хранилище 1.10.4 и ниже — только для одноразовой миграции."""
    base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return (base / "LeshyAI" / "LeshyBrain").resolve()


def main() -> None:
    parser = argparse.ArgumentParser(prog="Leshy")
    parser.add_argument("--host", default=os.getenv("LESHY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LESHY_PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    install_root = _install_root()
    brain_root = _brain_root()
    legacy_brain = _legacy_brain()

    # Пользователь выбирает место установки. Все изменяемые данные Лешего
    # живут только относительно Leshy.exe: .\LeshyBrain\...
    os.environ["LESHY_INSTALL_ROOT"] = str(install_root)
    os.environ["LESHY_BRAIN_DIR"] = str(brain_root)

    # Без потери данных: старое AppData-хранилище копируется один раз в новую
    # относительную папку. После успешной проверки источник не удаляется.
    if legacy_brain.exists() and not brain_root.exists():
        os.environ["LESHY_LEGACY_BRAIN_DIR"] = str(legacy_brain)
    else:
        os.environ.pop("LESHY_LEGACY_BRAIN_DIR", None)

    os.environ["LESHY_HOST"] = args.host
    os.environ["LESHY_PORT"] = str(args.port)

    from leshy.web_server import serve

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
