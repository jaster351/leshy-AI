"""Owner-bound SuperAgent dashboard operations used by the local Web UI.

All identity comes from the server-side Web session; request bodies never
control owner_id. Existing service methods enforce owner and state contracts.
"""
from __future__ import annotations

from typing import Any
from .storage import utcnow

_ARCHIVE_SCHEMA = """CREATE TABLE IF NOT EXISTS superagent_dashboard_archive (
    ticket_id TEXT PRIMARY KEY REFERENCES superagent_tickets(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    archived_at TEXT NOT NULL
)"""


def _archive_connection(service: Any):
    storage = getattr(service, "storage", None)
    if storage is None:
        raise ValueError("Архив недоступен без хранилища SuperAgent")
    return storage.connection()


def _ticket_rows(service: Any, owner_id: str, archived: bool, limit: int) -> list[dict[str, Any]]:
    if getattr(service, "storage", None) is None:
        return [] if archived else service.list(owner_id, limit=limit)
    with _archive_connection(service) as conn:
        conn.execute(_ARCHIVE_SCHEMA)
        condition = "IS NOT NULL" if archived else "IS NULL"
        rows = conn.execute(
            "SELECT t.id,t.title,t.state,t.latest_report,t.attention,t.attempt,t.created_at,t.updated_at "
            "FROM superagent_tickets AS t LEFT JOIN superagent_dashboard_archive AS a "
            "ON t.id=a.ticket_id AND a.owner_id=t.owner_id "
            f"WHERE t.owner_id=? AND a.ticket_id {condition} "
            "ORDER BY t.updated_at DESC LIMIT ?",
            (owner_id, max(1, min(int(limit), 100))),
        ).fetchall()
    return [dict(row) for row in rows]


def _set_archive(service: Any, owner_id: str, ticket_id: str, archived: bool) -> None:
    with _archive_connection(service) as conn:
        conn.execute(_ARCHIVE_SCHEMA)
        if archived:
            conn.execute(
                "INSERT OR REPLACE INTO superagent_dashboard_archive(ticket_id,owner_id,archived_at) "
                "SELECT id,owner_id,? FROM superagent_tickets WHERE id=? AND owner_id=?",
                (utcnow(), ticket_id, owner_id),
            )
        else:
            conn.execute(
                "DELETE FROM superagent_dashboard_archive WHERE ticket_id=? AND owner_id=?",
                (ticket_id, owner_id),
            )


_STATE_LABELS = {
    "queued": "В очереди", "working": "Выполняется", "waiting": "Ждёт Вашего ответа",
    "ready": "Завершено", "blocked": "Требует внимания", "failed": "Ошибка",
    "stopped": "Остановлено",
}
_RETRY_STATES = frozenset({"failed", "blocked", "waiting"})
_STOP_STATES = frozenset({"queued", "working", "waiting", "blocked", "failed"})
_PLAN_ACTIONS = {"plan_pause": "pause", "plan_resume": "resume", "plan_cancel": "cancel"}


def _present(item: dict[str, Any], *, archived: bool = False) -> dict[str, Any]:
    state = str(item.get("state") or "")
    return {
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or "")[:500],
        "state": state,
        "state_label": _STATE_LABELS.get(state, state or "Неизвестно"),
        "attempt": int(item.get("attempt") or 0),
        "latest_report": str(item.get("latest_report") or "")[:3000],
        "attention": str(item.get("attention") or "")[:2000] if state == "waiting" else "",
        "archived": archived,
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "can_stop": state in _STOP_STATES,
        "can_retry": state in _RETRY_STATES,
        "can_answer": state == "waiting" and not archived,
        "can_close": not archived,
        "can_restore": archived,
    }


def list_tickets(service: Any, owner_id: str, *, limit: int = 50, archived: bool = False) -> dict[str, Any]:
    """Read-only, owner-filtered list. Archiving preserves reports and steps."""
    return {"tasks": [_present(item, archived=archived) for item in _ticket_rows(service, owner_id, archived, limit)]}


def ticket_details(service: Any, owner_id: str, ticket_id: str) -> dict[str, Any]:
    item = service.get(ticket_id, owner_id=owner_id)
    result = _present(item)
    # The owner is verified above, before reading private message history.
    history = service._inputs(ticket_id) if hasattr(service, "_inputs") else []
    result["conversation"] = [
        {"role": "user", "text": str(entry.get("content") or "")[:12000],
         "created_at": str(entry.get("created_at") or "")}
        for entry in history[-40:] if entry.get("kind") in {"request", "human"}
    ]
    result["steps"] = [
        {"tool": str(step.get("tool") or "")[:100],
         "status": str(step.get("status") or "")[:40],
         "attempt": int(step.get("attempt") or 0)}
        for step in (item.get("steps") or [])[:30] if isinstance(step, dict)
    ]
    try:
        plan = service.plans.snapshot(ticket_id, owner_id)
    except KeyError:
        plan = None
    if plan:
        result["plan"] = {
            "state": plan.get("state"),
            "steps": [{"id": str(step.get("id") or "")[:64],
                       "title": str(step.get("title") or "")[:300],
                       "state": str(step.get("state") or "")[:40]}
                      for step in plan.get("steps", [])[:64]],
        }
    return {"task": result}


def execute_action(service: Any, owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Ожидается объект запроса")
    action = payload.get("action")
    if not isinstance(action, str):
        raise ValueError("Не указано действие")
    if action == "create":
        value = payload.get("text")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Опишите задачу")
        return {"task": _present(service.create(owner_id, "", value.strip()))}
    ticket_id = payload.get("id")
    if not isinstance(ticket_id, str) or not ticket_id.startswith("SA-") or len(ticket_id) > 100:
        raise ValueError("Некорректный идентификатор задания")
    # The read is mandatory even for plan actions: never trust client flags.
    ticket = service.get(ticket_id, owner_id=owner_id)
    state = str(ticket.get("state") or "")
    if action in {"close", "archive", "restore"}:
        if action == "restore":
            _set_archive(service, owner_id, ticket_id, False)
            return {"task": _present(service.get(ticket_id, owner_id=owner_id))}
        if action == "archive" and state not in {"ready", "failed", "stopped"}:
            raise ValueError("Сначала остановите незавершённое задание")
        if action == "close" and state not in {"ready", "failed", "stopped"}:
            service.stop_ticket(ticket_id, owner_id)
        _set_archive(service, owner_id, ticket_id, True)
        return {"task": _present(service.get(ticket_id, owner_id=owner_id), archived=True)}
    if action == "stop":
        if state not in _STOP_STATES:
            raise ValueError("Это задание уже завершено или остановлено")
        return {"task": _present(service.stop_ticket(ticket_id, owner_id))}
    if action == "retry":
        if state not in _RETRY_STATES:
            raise ValueError("Повтор доступен только после ошибки или ожидания")
        return {"task": _present(service.retry(ticket_id, owner_id))}
    if action == "answer":
        if state != "waiting":
            raise ValueError("Задание не ожидает ответа")
        value = payload.get("text")
        if not isinstance(value, str) or not value.strip() or len(value) > 12000:
            raise ValueError("Введите ответ (до 12000 символов)")
        return {"task": _present(service.answer(ticket_id, owner_id, value.strip()))}
    if action == "plan_create":
        if state in {"ready", "failed", "stopped"}:
            raise ValueError("План нельзя добавлять к завершённому заданию")
        steps = payload.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
            raise ValueError("Нужно от 1 до 32 этапов")
        result = service.plans.create(ticket_id, owner_id, steps)
        return {"plan": {"id": str(result["id"]), "state": str(result["state"])}}
    if action in _PLAN_ACTIONS:
        plan = service.plans.control(ticket_id, owner_id, _PLAN_ACTIONS[action])
        return {"plan": {"id": str(plan["id"]), "state": str(plan["state"])}}
    raise ValueError("Неизвестное действие SuperAgent")
