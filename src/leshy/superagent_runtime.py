from __future__ import annotations

import time
from typing import Any, Iterator

from .diagnostics import event, problem
from .module_settings import module_enabled
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow
from .superagent import SuperAgentIntent, SuperAgentService, format_ticket, parse_superagent_intent


_INSTALLED = False
_ORIGINAL_STREAM = None
_ORIGINAL_CHAT = None
_ORIGINAL_STOP_BACKGROUND = None


def _enabled(app) -> bool:
    try:
        return module_enabled(app.storage, "superagent")
    except Exception as exc:
        problem("superagent_module_setting_failed", str(exc))
        return True


def _service(app) -> SuperAgentService:
    service = getattr(app, "superagent", None)
    if not isinstance(service, SuperAgentService):
        service = SuperAgentService(app)
        app.superagent = service
    if _enabled(app):
        service.start()
    return service


def sync_superagent_runtime_state(app) -> None:
    service = getattr(app, "superagent", None)
    if not isinstance(service, SuperAgentService):
        if _enabled(app):
            _service(app)
        return
    if _enabled(app):
        service.start()
    else:
        service.stop()


def _save_user(app, message: str, owner_id: str, session_id: str | None) -> str:
    sid = app._ensure_session(owner_id, session_id)
    now = utcnow()
    with app.storage.connection() as conn:
        conn.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
            (sid, "user", message, now),
        )
        count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
        if count == 1:
            conn.execute(
                "UPDATE sessions SET title=?,title_source=?,updated_at=? WHERE id=?",
                (" ".join(message.split())[:80], "auto", now, sid),
            )
    return sid


def _execute(service: SuperAgentService, intent: SuperAgentIntent, owner_id: str, session_id: str) -> str:
    if intent.action == "create":
        ticket = service.create(owner_id, session_id, intent.argument)
        return (
            f"Принял автономную задачу {ticket['id']}. Работаю в фоне; состояние сохранится после перезапуска. "
            "Сообщу результат в этом чате, а если без Вашего решения нельзя продолжить — задам один конкретный вопрос."
        )
    if intent.action == "list":
        items = service.list(owner_id)
        if not items:
            return "У SuperAgent пока нет заданий. Новое задание: /agent <что нужно сделать>."
        return "Задания SuperAgent:\n" + "\n".join(format_ticket(item) for item in items)
    if intent.action == "status":
        return format_ticket(service.get(intent.ticket_id, owner_id=owner_id))
    if intent.action == "stop":
        return "Остановил. " + format_ticket(service.stop_ticket(intent.ticket_id, owner_id))
    if intent.action == "retry":
        return "Поставил на повтор. " + format_ticket(service.retry(intent.ticket_id, owner_id))
    if intent.action == "answer":
        ticket = service.answer(intent.ticket_id, owner_id, intent.argument)
        return f"Ответ передан в {ticket['id']}. Продолжаю автономную работу."
    raise ValueError("Неизвестная команда SuperAgent")


def install_superagent_routing(app_class, app=None) -> None:
    """Подключить SuperAgent как отдельный явный fast-path поверх обычного чата."""
    global _INSTALLED, _ORIGINAL_STREAM, _ORIGINAL_CHAT, _ORIGINAL_STOP_BACKGROUND

    if app is not None:
        _service(app)
        sync_superagent_runtime_state(app)
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_STOP_BACKGROUND = app_class.stop_background

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        value = str(message or "").strip()
        owner = str(owner_id or "web:local").strip()[:200]
        intent = parse_superagent_intent(value) if _enabled(self.app) else None
        if intent is None:
            yield from _ORIGINAL_STREAM(
                self, value, owner_id=owner, session_id=session_id, request_id=request_id
            )
            return

        rid, cancel = self._register(request_id)
        sid = ""
        started = time.monotonic()
        try:
            sid = _save_user(self.app, value, owner, session_id)
            yield StreamEvent("start", {
                "request_id": rid,
                "session_id": sid,
                "superagent": True,
            })
            if cancel.is_set():
                yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": "", "sources": [], "stopped": True})
                return
            answer = _execute(_service(self.app), intent, owner, sid)
            for chunk in self._chunk_fallback(answer, 180):
                if cancel.is_set():
                    break
                yield StreamEvent("delta", {"text": chunk})
            stopped = cancel.is_set()
            answered_at = self.app._save_assistant(sid, answer) if answer and not stopped else utcnow()
            latency_ms = int((time.monotonic() - started) * 1000)
            event(
                "superagent_chat_command",
                request_id=rid,
                session_id=sid,
                owner_id=owner,
                action=intent.action,
                ticket_id=intent.ticket_id,
                latency_ms=latency_ms,
            )
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer if not stopped else "",
                "created_at": answered_at,
                "sources": [],
                "stopped": stopped,
                "superagent": True,
                "latency_ms": latency_ms,
            })
        except (KeyError, ValueError, PermissionError) as exc:
            answer = str(exc).strip("'")
            if sid:
                self.app._save_assistant(sid, answer)
            yield StreamEvent("delta", {"text": answer})
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "sources": [],
                "stopped": False,
                "superagent": True,
            })
        except Exception as exc:
            problem("superagent_chat_stream_failed", str(exc), request_id=rid, session_id=sid, owner_id=owner)
            yield StreamEvent("error", {"request_id": rid, "session_id": sid, "error": str(exc), "superagent": True})
        finally:
            self._unregister(rid)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None) -> dict[str, Any]:
        value = str(message or "").strip()
        owner = str(owner_id or "web:local").strip()[:200]
        intent = parse_superagent_intent(value) if _enabled(self) else None
        if intent is None:
            return _ORIGINAL_CHAT(self, value, owner_id=owner, session_id=session_id)
        sid = _save_user(self, value, owner, session_id)
        answer = _execute(_service(self), intent, owner, sid)
        created_at = self._save_assistant(sid, answer)
        event("superagent_chat_command", session_id=sid, owner_id=owner, action=intent.action, ticket_id=intent.ticket_id)
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": created_at,
            "sources": [],
            "superagent": True,
        }

    def stop_background(self) -> None:
        service = getattr(self, "superagent", None)
        if isinstance(service, SuperAgentService):
            try:
                service.stop()
            except Exception as exc:
                problem("superagent_stop_failed", str(exc))
        return _ORIGINAL_STOP_BACKGROUND(self)

    RealtimeChatManager.stream = stream
    app_class.chat = chat
    app_class.stop_background = stop_background


__all__ = ["install_superagent_routing", "sync_superagent_runtime_state"]
