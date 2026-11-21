"""Read-only snapshot of Leshy's automatic model provisioning.

Every file is anchored under LeshyBrain/runtime/model-provision. No commands,
remote URLs, model loading or dependency imports are triggered by an API poll.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

STAGES = ("inferssd", "inkling", "inference-dependencies", "moe")
MAX_FILE_BYTES = 1_048_576
# Model downloads may take hours. A task that has gone a full day without
# updating its stage marker is no longer reported as definitively running.
STALE_RUNNING_SECONDS = 24 * 3600


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        if path.stat().st_size > MAX_FILE_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _redact(value: object) -> str:
    """Do not expose credentials embedded in pip/HF/proxy errors."""
    text = str(value or "")[:700]
    text = re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1***@", text)
    text = re.sub(r"(?i)(hf_[a-z0-9]{12,})", "[HF token redacted]", text)
    return text


def _safe_stage(root: Path, name: str, now: datetime) -> dict[str, Any]:
    record = _read_json(root / (name + ".json"))
    outcome = str(record.get("outcome") or "").strip().lower()
    if outcome not in {"running", "failed", "downloaded", "installed", "ready", "skipped"}:
        return {"stage": name, "outcome": "not_started"}
    updated_at = str(record.get("updated_at") or "")
    if outcome == "running":
        try:
            since = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            if (now - since).total_seconds() > STALE_RUNNING_SECONDS:
                outcome = "stale"
        except ValueError:
            outcome = "unknown"
    return {
        "stage": name,
        "outcome": outcome,
        "updated_at": updated_at,
        "details": _redact(record.get("details")),
    }


def _health_summary(root: Path) -> dict[str, Any]:
    data = _read_json(root / "storage-health.json")
    if not data or data.get("schema") != 1:
        return {"available": False}
    original = data.get("models") or {}
    engine = data.get("native_engine") or {}
    stream = data.get("ssd_streaming") or {}
    space = data.get("storage") or {}
    free = {}
    for key, value in space.items():
        if not isinstance(value, dict):
            continue
        item = {"path": str(value.get("path") or "")[:1024]}
        amount = value.get("free_bytes")
        if type(amount) is int and amount >= 0:
            item["free_gib"] = round(amount / 1024**3, 2)
        free[str(key)] = item
    inkling = original.get("inkling_original") or {}
    moe = original.get("inferssd_moe") or {}
    return {
        "available": True,
        "updated_at": str(data.get("updated_at") or ""),
        "inkling_state": str(inkling.get("state") or "unknown"),
        "moe_state": str(moe.get("state") or "unknown"),
        "native_engine_installed": engine.get("installed") is True,
        "ssd_streaming_verified": stream.get("ssd_streaming_verified") is True,
        "ssd_read_bytes_observed": int(stream.get("ssd_read_bytes_observed") or 0)
        if type(stream.get("ssd_read_bytes_observed") or 0) is int else 0,
        "free": free,
    }


def model_provision_status(brain_dir: Path | str) -> dict[str, Any]:
    root = Path(brain_dir).expanduser().resolve() / "runtime" / "model-provision"
    now = datetime.now(timezone.utc)
    stages = [_safe_stage(root, item, now) for item in STAGES]
    linux_state = root / "linux-state.txt"
    linux = {"outcome": "not_started"}
    try:
        if linux_state.is_file() and not linux_state.is_symlink() and linux_state.stat().st_size < 2048:
            line = linux_state.read_text(encoding="utf-8").splitlines()[0].strip()
            flag = line.split(" ", 1)[0]
            if flag in {"complete", "complete-v2", "failed"}:
                linux = {
                    "outcome": flag,
                    "updated_at": datetime.fromtimestamp(
                        linux_state.stat().st_mtime, timezone.utc
                    ).isoformat(),
                }
    except (OSError, ValueError, IndexError, UnicodeError):
        pass
    return {
        "schema": 1,
        "updated_at": now.isoformat(),
        "stages": stages,
        "linux": linux,
        "health": _health_summary(root),
        "note": (
            "A downloaded model or healthy API is not evidence of actual "
            "InferSSD expert streaming; verified telemetry is required."
        ),
    }
