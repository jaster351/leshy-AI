from __future__ import annotations

import uuid
from typing import Iterator

from .diagnostics import problem
from .phone_osint import PhoneOsintService, is_phone_osint_request
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow


_INSTALLED = False
_ORIGINAL_CHAT = None
_ORIGINAL_STREAM = None


def _service(app) -> PhoneOsintService:
    current = getattr(app, "phone_osint", None)
    if not isinstance(current, PhoneOsintService):
        current = PhoneOsintService(app)
        app.phone_osint = current
    return current


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


def install_phone_osint_runtime(app_class) -> None:
    """Подключить пассивный антифрод OSINT к Web/Telegram без изменения LLM."""
    global _INSTALLED, _ORIGINAL_CHAT, _ORIGINAL_STREAM
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_STREAM = RealtimeChatManager.stream

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None, **kwargs):
        value = str(message or "").strip()
        if not is_phone_osint_request(value):
            return _ORIGINAL_CHAT(self, message, owner_id=owner_id, session_id=session_id, **kwargs)

        owner_id = (owner_id or "web:local").strip()[:200]
        sid = _save_user(self, value, owner_id, session_id)
        try:
            result = _service(self).analyze(value)
            answer = PhoneOsintService.format_answer(result)
        except Exception as exc:  # noqa: BLE001 — внешний поиск не должен ломать чат.
            problem("phone_osint_chat_failed", str(exc), session_id=sid, owner_id=owner_id)
            result = {"error": str(exc), "evidence": []}
            answer = "Не удалось выполнить OSINT-проверку номера: " + str(exc)

        sources = [
            {"title": str(item.get("title") or item.get("host") or "Источник"), "url": str(item.get("url") or "")}
            for item in list(result.get("evidence") or [])
            if str(item.get("url") or "").startswith(("http://", "https://"))
        ]
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": self._save_assistant(sid, answer),
            "sources": sources,
            "phone_osint": result,
        }

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        value = str(message or "").strip()
        if not is_phone_osint_request(value):
            yield from _ORIGINAL_STREAM(
                self,
                message,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

        rid, cancel = self._register(request_id or str(uuid.uuid4()))
        try:
            yield StreamEvent("activity", {
                "kind": "phone_osint",
                "phase": "searching",
                "text": "Проверяю номер по открытым источникам и антифрод-сигналам…",
            })
            result = self.app.chat(value, owner_id=owner_id, session_id=session_id)
            sid = str(result.get("session_id") or "")
            answer = str(result.get("answer") or "")
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "phone_osint": True})
            if not cancel.is_set() and answer:
                for chunk in self._chunk_fallback(answer, 180):
                    if cancel.is_set():
                        break
                    yield StreamEvent("delta", {"text": chunk})
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": result.get("created_at"),
                "sources": result.get("sources") or [],
                "phone_osint": result.get("phone_osint") or {},
                "stopped": bool(cancel.is_set()),
            })
        except Exception as exc:  # noqa: BLE001
            problem("phone_osint_stream_failed", str(exc), request_id=rid, owner_id=owner_id)
            yield StreamEvent("error", {"request_id": rid, "error": str(exc), "phone_osint": True})
        finally:
            self._unregister(rid)

    app_class.chat = chat
    RealtimeChatManager.stream = stream


__all__ = ["install_phone_osint_runtime"]
