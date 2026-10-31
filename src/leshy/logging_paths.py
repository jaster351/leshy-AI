from __future__ import annotations

import os
from pathlib import Path


def resolve_brain_root(base_dir: Path) -> Path:
    """Return the canonical LeshyBrain root for an app/data/brain path."""
    base = Path(base_dir)
    if base.name == "LeshyBrain":
        return base
    if base.name.lower() == "leshy":
        return base / "data" / "LeshyBrain"
    return base


def central_log_dir(base_dir: Path) -> Path:
    """Single runtime logging root used by every Leshy component."""
    path = resolve_brain_root(Path(base_dir)) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def central_log_path(base_dir: Path, filename: str) -> Path:
    """Return a file inside the central log root and reject nested/absolute paths."""
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.name != filename or filename in {"", ".", ".."}:
        raise ValueError("Leshy log files must be direct children of the central logs directory")
    return central_log_dir(base_dir) / filename


def analysis_log_dir() -> Path | None:
    """Return the developer-analysis log folder when one is available.

    The canonical runtime logs remain in LeshyBrain/logs. This secondary destination is
    used only for sanitized copies that the developer/ChatGPT can inspect. An installed
    build can opt in with LESHY_ANALYSIS_LOG_DIR; a source checkout is detected by the
    repository VERSION file plus log/README.md sentinel.
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
