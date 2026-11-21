"""Read-only evidence of *actual* InferSSD SSD expert streaming.

Do not infer streaming from a running process, a model filename, a GGUF magic
header, an API health response, or human-readable logs. This only recognizes
native v1 observation events in a run that names this exact local GGUF.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

MAX_RUNS = 24
MAX_EVENTS = 100_000
MAX_EVENT_LINE = 512 * 1024
RECENT_SECONDS = 24 * 3600


def infer_home() -> Path:
    explicit = os.environ.get("INFERSSD_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return (Path(base) / "inferssd").resolve()
    return (Path.home() / ".local" / "share" / "inferssd").resolve()


def _model_matches_run(directory: Path, model: Path) -> bool:
    """Bind native observations to a specific model, never another agent run."""
    receipt = directory / "runtime.json"
    try:
        if not receipt.is_file() or receipt.stat().st_size > 1024 * 1024:
            return False
        contents = json.loads(receipt.read_text(encoding="utf-8"))
        argv = contents.get("argv")
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            return False
        for index, token in enumerate(argv[:-1]):
            if token in ("-m", "--model") and Path(argv[index + 1]).expanduser().resolve() == model:
                return True
    except (ValueError, OSError, TypeError):
        pass
    return False


def _read_native_events(path: Path) -> dict[str, Any]:
    """Accept upstream telemetry v1 envelope and meaningful positive SSD reads."""
    result: dict[str, Any] = {
        "contract": False,
        "routed_layers": 0,
        "positive_ssd_read_bytes": 0,
        "complete": False,
    }
    sequence = 0
    saw_contract = False
    routed = 0
    read_bytes = 0
    try:
        if not path.is_file() or path.stat().st_size < 2:
            return result
        with path.open("r", encoding="utf-8", errors="strict") as stream:
            for _ in range(MAX_EVENTS):
                line = stream.readline(MAX_EVENT_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_EVENT_LINE or not line.endswith("\n"):
                    # A partially written final line is not evidence.
                    break
                event = json.loads(line)
                if not isinstance(event, dict):
                    return result
                if type(event.get("v")) is not int or event["v"] != 1:
                    return result
                if type(event.get("seq")) is not int or event["seq"] != sequence:
                    return result
                sequence += 1
                kind = event.get("ev")
                if kind == "ssd.controller.begin":
                    if saw_contract or type(event.get("schema")) is not int or event["schema"] != 1:
                        return result
                    saw_contract = True
                elif kind == "ssd.place.layer" and saw_contract:
                    chosen = event.get("selected_experts")
                    vram = event.get("vram_hit")
                    cached = event.get("cache_hit")
                    if (
                        event.get("success") is True
                        and event.get("decode") is True
                        and event.get("has_token") is True
                        and isinstance(chosen, list)
                        and bool(chosen)
                        and all(type(x) is int and x >= 0 for x in chosen)
                        and isinstance(vram, list)
                        and isinstance(cached, list)
                        and len(chosen) == len(vram) == len(cached)
                        and all(type(x) is bool for x in (*vram, *cached))
                    ):
                        routed += 1
                elif kind == "ssd.prefill.layer" and saw_contract:
                    size = event.get("read_bytes")
                    if type(size) is int and size > 0:
                        read_bytes += size
                if saw_contract and routed > 0 and read_bytes > 0:
                    return {
                        "contract": True,
                        "routed_layers": routed,
                        "positive_ssd_read_bytes": read_bytes,
                        "complete": True,
                    }
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return result
    result.update(
        contract=saw_contract,
        routed_layers=routed,
        positive_ssd_read_bytes=read_bytes,
    )
    return result


def native_streaming_status(model: Path) -> dict[str, Any]:
    """Evidence is returned only for a recent matching local native run."""
    target = Path(model).expanduser().resolve()
    roots = infer_home() / "state" / "runs"
    default = {
        "ssd_streaming_verified": False,
        "source": "native-events.jsonl",
        "model": str(target),
        "reason": "No recent matching native SSD read and routed expert events",
    }
    try:
        if not target.is_file() or not roots.is_dir():
            return default
        candidates = sorted(
            (p for p in roots.iterdir() if p.is_dir() and not p.is_symlink()),
            key=lambda p: p.stat().st_mtime_ns,
            reverse=True,
        )[:MAX_RUNS]
        now = time.time()
        for directory in candidates:
            events = directory / "native-events.jsonl"
            if not events.is_file() or events.is_symlink():
                continue
            if now - events.stat().st_mtime > RECENT_SECONDS:
                continue
            if not _model_matches_run(directory, target):
                continue
            evidence = _read_native_events(events)
            if evidence["complete"]:
                return {
                    "ssd_streaming_verified": True,
                    "source": "native-events.jsonl",
                    "model": str(target),
                    "run_id": directory.name,
                    "routed_layers_observed": evidence["routed_layers"],
                    "ssd_read_bytes_observed": evidence["positive_ssd_read_bytes"],
                    "observation_mtime": events.stat().st_mtime,
                }
    except (OSError, ValueError, TypeError):
        return default
    return default
