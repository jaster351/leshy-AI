from __future__ import annotations

import os
from pathlib import Path


def resolve_brain_root(base_dir: Path) -> Path:
    """Вернуть канонический корень LeshyBrain для пути приложения, данных или мозга."""
    base = Path(base_dir)
    if base.name == "LeshyBrain":
        return base
    if base.name.lower() == "leshy":
        return base / "data" / "LeshyBrain"
    return base


def central_log_dir(base_dir: Path) -> Path:
    """Вернуть единый каталог журналов, которым пользуются все компоненты Лешего."""
    path = resolve_brain_root(Path(base_dir)) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def central_log_path(base_dir: Path, filename: str) -> Path:
    """Вернуть файл внутри единого каталога журналов и запретить вложенные и абсолютные пути."""
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.name != filename or filename in {"", ".", ".."}:
        raise ValueError("Файлы журналов Лешего должны находиться непосредственно в едином каталоге logs")
    return central_log_dir(base_dir) / filename


def analysis_log_dir() -> Path | None:
    """Вернуть каталог для обезличенных копий журналов разработчика, если он доступен.

    Канонические рабочие журналы всегда остаются в LeshyBrain/logs. Вторичный каталог
    используется только для очищенных от чувствительных данных копий, которые можно
    анализировать при разработке. Установленная сборка может явно задать
    LESHY_ANALYSIS_LOG_DIR, а исходный репозиторий определяется по VERSION и log/README.md.
    """
    configured = os.environ.get("LESHY_ANALYSIS_LOG_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / "VERSION").is_file() and (parent / "log" / "README.md").is_file():
            path = parent / "log"
            path.mkdir(parents=True, exist_ok=True)
            return path
    return None
