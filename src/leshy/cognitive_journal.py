"""Персистентный журнал шагов SuperAgent: минимум данных, без секретов.

Записи не содержат исходные аргументы, результаты запросов и ответы LLM.
Пока разрешены только операции чтения, которые можно безопасно повторить
в новом assignment после сбоя процесса.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .storage import Storage, utcnow


_SCHEMA = """
CREATE TABLE IF NOT EXISTS superagent_steps(
    ticket_id TEXT NOT NULL REFERENCES superagent_tickets(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL CHECK(attempt > 0),
    step_index INTEGER NOT NULL CHECK(step_index > 0),
    tool TEXT NOT NULL,
    argument_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('started','success','failed','interrupted')),
    receipt_id TEXT NOT NULL DEFAULT '',
    result_sha256 TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(ticket_id,attempt,step_index)
);
CREATE INDEX IF NOT EXISTS ix_superagent_steps_ticket
ON superagent_steps(ticket_id,attempt DESC,step_index DESC);
"""


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class CognitiveStepJournal:
    def __init__(self, storage: Storage):
        self.storage = storage
        with self.storage.connection() as conn:
            conn.executescript(_SCHEMA)

    def begin(self, ticket_id: str, attempt: int, step_index: int,
              tool: str, arguments: dict[str, Any]) -> None:
        if int(attempt) < 1 or int(step_index) < 1:
            raise ValueError("Некорректный номер попытки или шага")
        with self.storage.connection() as conn:
            # Duplicate primary key must reject the call; no INSERT OR REPLACE.
            conn.execute(
                "INSERT INTO superagent_steps("
                "ticket_id,attempt,step_index,tool,argument_sha256,status,started_at"
                ") VALUES(?,?,?,?,?,'started',?)",
                (str(ticket_id), int(attempt), int(step_index), str(tool),
                 _digest(arguments), utcnow()),
            )

    def finish(self, ticket_id: str, attempt: int, step_index: int, *,
               status: str, receipt_id: str = "", result: Any = None) -> None:
        if status not in {"success", "failed"}:
            raise ValueError("Недопустимое состояние завершения")
        if status == "success" and not str(receipt_id).strip():
            raise ValueError("Успешный шаг требует реальной квитанции")
        with self.storage.connection() as conn:
            changed = conn.execute(
                "UPDATE superagent_steps SET status=?,receipt_id=?,result_sha256=?,finished_at=? "
                "WHERE ticket_id=? AND attempt=? AND step_index=? AND status='started'",
                (status, str(receipt_id) if status == "success" else "",
                 _digest(result) if status == "success" else "",
                 utcnow(), str(ticket_id), int(attempt), int(step_index)),
            )
            if changed.rowcount != 1:
                raise ValueError("Шаг отсутствует или уже завершён")

    def list_steps(self, ticket_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT attempt,step_index,tool,status,started_at,finished_at "
                "FROM superagent_steps WHERE ticket_id=? "
                "ORDER BY attempt DESC,step_index DESC LIMIT ?",
                (str(ticket_id), max(1, min(int(limit), 50))),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["CognitiveStepJournal"]
