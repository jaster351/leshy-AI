from __future__ import annotations

import os
from pathlib import Path
import re
import sys

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _candidate_version_files() -> list[Path]:
    candidates: list[Path] = []

    explicit = os.getenv("LESHY_VERSION_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit))

    # Установленная/frozen-сборка: VERSION обязан лежать рядом с приложением
    # либо быть добавлен в PyInstaller bundle как data-файл.
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "VERSION")
        bundle_root = getattr(sys, "_MEIPASS", "")
        if bundle_root:
            candidates.append(Path(bundle_root) / "VERSION")

    # Исходное дерево: src/leshy/version.py -> <repo>/VERSION.
    candidates.append(Path(__file__).resolve().parents[2] / "VERSION")

    # Убираем дубликаты, сохраняя порядок.
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def version_file() -> Path:
    for path in _candidate_version_files():
        if path.is_file():
            return path
    searched = ", ".join(str(path) for path in _candidate_version_files())
    raise RuntimeError(
        "Не найден канонический файл VERSION. "
        f"Проверены пути: {searched}. Сборка Лешего неполна."
    )


def read_version() -> str:
    value = version_file().read_text(encoding="utf-8").strip()
    if not _VERSION_RE.fullmatch(value):
        raise RuntimeError(f"Некорректная версия в VERSION: {value!r}")
    return value


VERSION = read_version()
__version__ = VERSION


def version_payload() -> dict[str, str]:
    """Единый runtime-ответ для API/UI/диагностики и интеграций."""
    return {
        "name": "Леший",
        "version": VERSION,
        "source": str(version_file()),
    }


if __name__ == "__main__":
    print(VERSION)
