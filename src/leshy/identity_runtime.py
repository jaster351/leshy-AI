from __future__ import annotations

import base64
import re
import time
from typing import Any, Iterator

from .creator_identity_data import CREATOR_NAME_B64
from .diagnostics import event, problem
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow


CREATOR_FULL_NAME = base64.b64decode(CREATOR_NAME_B64, validate=True).decode("utf-8")
CREATOR_ANSWER = f"Мой создатель — {CREATOR_FULL_NAME}."

_INSTALLED = False
_ORIGINAL_STREAM = None
_ORIGINAL_CHAT = None


def creator_identity_answer(message: str) -> str | None:
    """Вернуть канонический ответ на явный вопрос об авторе/создателе.

    Это локальный fast-path: он не запускает LLM, интернет-поиск или модель,
    поэтому сведения об идентичности Лешего доступны мгновенно даже при
    недоступном inference backend.
    """
    value = " ".join(str(message or "").lower().replace("ё", "е").split())
    value = re.sub(r"[^а-яa-z0-9\s-]", "", value).strip()
    markers = (
        "кто твой создатель",
        "кто твой автор",
        "кто твой разработчик",
        "кто тебя создал",
        "кто тебя сделал",
        "кто создал лешего",
        "кто автор лешего",
        "кто разработчик лешего",
        "назови своего создателя",
        "назови своего автора",
    )
    if any(marker in value for marker in markers):
        return CREATOR_ANSWER
    return None


def is_owner_identity_question(message: str) -> bool:
    """Личная идентификация — лишь по подтверждённому Telegram ID.

    Имя/аватар Telegram и слова «я хозяин» не подтверждают владельца.
    Сам этот предикат не выдаёт никаких полномочий.
    """
    value = " ".join(str(message or "").lower().replace("ё", "е").split())
    value = re.sub(r"[^а-яa-z0-9\s]", " ", value)
    value = " ".join(value.split())
    return any(phrase in value for phrase in (
        "а я по твоему кто",
        "я по твоему кто",
        "ты знаешь кто я",
        "ты знаешь кто я такой",
        "кто я для тебя",
        "кто я такой для тебя",
        "ты меня узнаешь",
        "ты меня узнал",
        "ты меня узнала",
    ))


def _save_direct_user(app, message: str, owner_id: str, session_id: str | None) -> str:
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


def install_creator_identity_routing(app_class) -> None:
    """Подключить единый fast-path идентичности для Web/Windows/Telegram."""
    global _INSTALLED, _ORIGINAL_STREAM, _ORIGINAL_CHAT
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_CHAT = app_class.chat

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        value = str(message or "").strip()
        answer = creator_identity_answer(value)
        if answer is None:
            yield from _ORIGINAL_STREAM(
                self,
                value,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

        owner = (owner_id or "web:local").strip()[:200]
        rid, cancel = self._register(request_id)
        sid = ""
        started = time.monotonic()
        try:
            sid, _rows = self._save_user_and_history(value, owner, session_id)
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "identity_fastpath": True})
            if not cancel.is_set():
                yield StreamEvent("delta", {"text": answer})
            answered_at = self.app._save_assistant(sid, answer)
            latency_ms = int((time.monotonic() - started) * 1000)
            event(
                "creator_identity_answered",
                request_id=rid,
                session_id=sid,
                owner_id=owner,
                latency_ms=latency_ms,
            )
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": answered_at,
                "sources": [],
                "stopped": bool(cancel.is_set()),
                "identity_fastpath": True,
                "latency_ms": latency_ms,
            })
        except Exception as exc:
            problem("creator_identity_fastpath_failed", str(exc), request_id=rid, session_id=sid, owner_id=owner)
            yield StreamEvent("error", {"request_id": rid, "session_id": sid, "error": str(exc), "identity_fastpath": True})
        finally:
            self._unregister(rid)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None) -> dict[str, Any]:
        value = str(message or "").strip()
        answer = creator_identity_answer(value)
        if answer is None:
            return _ORIGINAL_CHAT(self, value, owner_id=owner_id, session_id=session_id)
        owner = (owner_id or "web:local").strip()[:200]
        sid = _save_direct_user(self, value, owner, session_id)
        created_at = self._save_assistant(sid, answer)
        event("creator_identity_answered", session_id=sid, owner_id=owner, latency_ms=0)
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": created_at,
            "sources": [],
            "identity_fastpath": True,
        }

    RealtimeChatManager.stream = stream
    app_class.chat = chat


__all__ = [
    "CREATOR_FULL_NAME",
    "CREATOR_ANSWER",
    "creator_identity_answer",
    "is_owner_identity_question",
    "install_creator_identity_routing",
]
