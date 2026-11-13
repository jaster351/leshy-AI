"""Resource-aware concurrency for independent SuperAgent tickets.

The local model's inference lock remains authoritative: more workers improve
parallel I/O and independent jobs, not simultaneous local LLM generations.
Each node sizes its own pool; cluster membership imposes no fixed node count.
"""
from __future__ import annotations

import os
from typing import Callable

_MIB = 1024 * 1024
_MAX_PER_NODE = 128
_MIN_FREE_BYTES = 512 * _MIB
_MEMORY_PER_JOB = 256 * _MIB


def worker_ceiling(configured: str | None = None, *, cpu_count: int | None = None) -> int:
    """Pick a safe per-node thread ceiling. Numeric override is still bounded."""
    cores = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)))
    automatic = max(2, min(64, cores * 2))
    value = os.getenv("LESHY_SUPERAGENT_MAX_WORKERS", "auto") if configured is None else str(configured)
    if not value or value.strip().lower() == "auto":
        return automatic
    try:
        number = int(value.strip())
    except (ValueError, TypeError):
        return automatic
    return automatic if number < 1 else min(number, _MAX_PER_NODE)


def _free_memory_bytes() -> int | None:
    try:
        import psutil  # type: ignore
        return max(0, int(psutil.virtual_memory().available))
    except Exception:
        # Unknown RAM is not mistaken for an unlimited amount. CPU/explicit
        # configuration stays the only concurrency input when psutil is absent.
        return None


def available_worker_slots(ceiling: int, *, free_memory_bytes: int | None = None,
                           memory_probe: Callable[[], int | None] | None = None) -> int:
    """Admission limit for NEW work. Already-running jobs are not killed.

    One worker consumes at least an estimated 256 MiB of headroom; leave an
    additional 512 MiB for the interactive chat, OS, and inference backend.
    Zero slots is valid under memory pressure and stops new admissions.
    """
    if type(ceiling) is not int or ceiling < 1 or ceiling > _MAX_PER_NODE:
        raise ValueError("Invalid worker ceiling")
    probe = memory_probe if memory_probe is not None else _free_memory_bytes
    available = probe() if free_memory_bytes is None else free_memory_bytes
    if available is None:
        return ceiling
    if type(available) is not int or available < 0:
        return 0
    slots = max(0, (available - _MIN_FREE_BYTES) // _MEMORY_PER_JOB)
    return min(ceiling, int(slots))


__all__ = ["worker_ceiling", "available_worker_slots"]
