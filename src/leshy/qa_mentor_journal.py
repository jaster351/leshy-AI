"""Проверяемый локальный журнал общения QA / AI-наставника с реальным Лешим.

Записи возникают исключительно после вызова app.chat(), не из клиентских
«успешных» отчётов. Хранилище находится в LeshyBrain и не публикуется в GitHub.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import uuid


_ROLES = {"qa": "QA-тестировщик", "mentor": "AI-наставник"}
_SECRET = re.compile(
    r"(?i)(?:(?:bearer|api[_ -]?token|password|passwd|secret|пароль|токен)"
    r"\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_+./=-]{12,}")
_MAX_RECORDS = 250


def _display_text(value: object, limit: int) -> str:
    value = str(value or "").replace("\x00", "")
    value = "".join(char for char in value if char in "\n\t" or ord(char) >= 32)
    value = _SECRET.sub(lambda match: match.group(0).replace(match.group(1), "[скрыто]"), value)
    value = _BEARER.sub("Bearer [скрыто]", value)
    return value[:limit]


class QAMentorJournal:
    """Потокобезопасный bounded JSONL, отдельный от истории пользователя."""

    def __init__(self, brain_dir: Path):
        self.path = Path(brain_dir) / "qa_mentor" / "live-dialogue.jsonl"
        self._lock = threading.RLock()

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            # Сканируем только ограниченный хвост файла.
            with self.path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 950_000))
                chunk = handle.read()
            if size > 950_000:
                chunk = chunk.split(b"\n", 1)[-1]
            rows = []
            for line in chunk.splitlines():
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get("source") == "live_api":
                        rows.append(row)
                except (UnicodeDecodeError, ValueError):
                    continue
            return rows[-_MAX_RECORDS:]
        except OSError:
            return []

    def list_recent(self, limit: int = 80) -> dict:
        with self._lock:
            rows = self._load()
            return {
                "source": "live_api",
                "count": len(rows),
                "items": rows[-max(1, min(int(limit), 150)):],
                "last_activity_at": rows[-1]["finished_at"] if rows else None,
            }

    def append_real_exchange(
        self, *, role: str, request: str, answer: str = "",
        session_id: str = "", started_at: str, elapsed_ms: int,
        status: str, error_type: str = "",
    ) -> dict:
        if role not in _ROLES or status not in {"answered", "error"}:
            raise ValueError("Неверная роль или состояние QA-события")
        row = {
            "id": uuid.uuid4().hex,
            "source": "live_api",
            "author": _ROLES[role],
            "role": role,
            "responder": "Леший",
            "request": _display_text(request, 1600),
            "answer": _display_text(answer, 4000) if status == "answered" else "",
            "session_id": _display_text(session_id, 120),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": max(0, int(elapsed_ms)),
            "status": status,
            "verdict": "not_evaluated",
            "error_type": _display_text(error_type, 80),
        }
        with self._lock:
            rows = self._load()
            rows.append(row)
            rows = rows[-_MAX_RECORDS:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            candidate = self.path.with_suffix(".jsonl.tmp")
            with candidate.open("w", encoding="utf-8", newline="\n") as file:
                for item in rows:
                    file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            candidate.replace(self.path)
        return row
