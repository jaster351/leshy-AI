from __future__ import annotations

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
    """Single logging root used by every Leshy component."""
    path = resolve_brain_root(Path(base_dir)) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def central_log_path(base_dir: Path, filename: str) -> Path:
    """Return a file inside the central log root and reject nested/absolute paths."""
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.name != filename or filename in {"", ".", ".."}:
        raise ValueError("Leshy log files must be direct children of the central logs directory")
    return central_log_dir(base_dir) / filename
