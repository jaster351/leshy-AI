"""Ручная доставка LiveMentor через существующий планировщик Windows."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
from datetime import datetime, timezone
from uuid import uuid4, UUID

from .mentor_view import _read

_LOCK = threading.Lock()
_TASK = "Leshy AI - Git Build (15 min)"
_MAX_AGE = 30 * 60


def request_state(root: Path) -> dict:
    req = _read(root / "delivery-request.json")
    if req.get("schema") != "ai-mentor.delivery-request.v1":
        return {}
    try:
        rid = str(UUID(req["request_id"]))
        if rid != req["request_id"]:
            return {}
        created = datetime.fromisoformat(req["created_at"])
        age = (datetime.now(timezone.utc) - created).total_seconds()
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}
    status = _read(root / "delivery-request-result.json")
    if status.get("schema") == "ai-mentor.delivery-status.v1" and status.get("request_id") == rid:
        try:
            checked = datetime.fromisoformat(status["timestamp"])
            if checked >= created and status.get("status") in {
                "not_configured", "disabled", "authorization_required", "authorization_error",
                "configuration_changed", "delivered", "up_to_date", "error", "not_confirmed",
            }:
                return {"state": "finished", "request_id": rid, "result": status["status"],
                        "created_at": req["created_at"], "checked_at": status["timestamp"]}
        except (KeyError, TypeError, ValueError):
            pass
    return {"state": "queued" if 0 <= age < _MAX_AGE else "unconfirmed",
            "request_id": rid, "created_at": req["created_at"]}


def _reparse(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def request_delivery(brain_dir: Path) -> dict:
    brain = Path(brain_dir)
    root = brain / "ai-mentor"
    if os.name != "nt":
        raise ValueError("Ручная доставка через обновлятор доступна на Windows")
    if not root.is_dir() or _reparse(brain) or _reparse(root):
        raise ValueError("Контур LiveMentor не настроен")
    resolved = brain.resolve()
    if len(resolved.parents) < 3:
        raise ValueError("Не найден штатный обновлятор")
    repo = resolved.parents[2]
    if resolved != repo / "dist" / "Leshy" / "LeshyBrain" or not (repo / ".git").exists():
        raise ValueError("Не найден штатный обновлятор этой установки")
    config = _read(root / "config.json")
    if config.get("enabled") is not True or config.get("repository") != "jaster351/AI-mentor":
        raise ValueError("Обмен LiveMentor не включён")
    path = root / "delivery-request.json"
    with _LOCK:
        if path.exists() or path.is_symlink():
            if _reparse(path):
                raise ValueError("Недопустимый файл запроса доставки")
        previous = request_state(root)
        if previous.get("state") == "queued":
            return previous
        request_id = str(uuid4())
        temp = root / ("delivery-request-" + request_id + ".tmp")
        try:
            with temp.open("x", encoding="utf-8") as f:
                json.dump({"schema": "ai-mentor.delivery-request.v1", "request_id": request_id,
                           "created_at": datetime.now(timezone.utc).isoformat()}, f)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        # Только фиксированная задача; параметры HTTP не становятся командой.
        exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe"
        try:
            result = subprocess.run([str(exe), "/Run", "/TN", _TASK],
                                    capture_output=True, timeout=12,
                                    creationflags=0x08000000, check=False)
            triggered = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            triggered = False
        # Запрос остаётся для следующего штатного запуска, даже если немедленный запуск не подтверждён.
        return {"state": "queued", "request_id": request_id, "triggered": triggered}
