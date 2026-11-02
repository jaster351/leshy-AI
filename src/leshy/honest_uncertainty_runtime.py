from __future__ import annotations

from typing import Any, Iterator

from .diagnostics import event, problem
from .realtime_chat import RealtimeChatManager, StreamEvent


_INSTALLED = False
_ORIGINAL_CHAT = None
_ORIGINAL_STREAM = None
_ORIGINAL_NEEDS_RESEARCH = None
_FALLBACK = "Не знаю. У меня нет достоверной информации, чтобы ответить на этот вопрос."


def _repair_saved_empty(app, session_id: str, answer: str) -> None:
    """Исправить только последний действительно пустой ответ ассистента."""
    if not session_id:
        return
    try:
        with app.storage.connection() as conn:
            row = conn.execute(
                "SELECT id,content FROM messages WHERE session_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is not None and not str(row["content"] or "").strip():
                conn.execute("UPDATE messages SET content=? WHERE id=?", (answer, int(row["id"])))
    except Exception as exc:
        problem("honest_uncertainty_history_repair_failed", str(exc), session_id=session_id)


def install_honest_uncertainty(app_class) -> None:
    """Глобальный последний предохранитель от пустых и скрыто-неопределённых ответов.

    Основной LLM по-прежнему может выполнить один обоснованный поиск через обычный
    chat runtime. Этот слой не запускает повторные поиски: если подтверждённого
    ответа так и нет или нижний runtime вернул пустой результат, пользователь
    получает прямое «не знаю», а не бесконечное ожидание или пустое сообщение.
    """
    global _INSTALLED, _ORIGINAL_CHAT, _ORIGINAL_STREAM, _ORIGINAL_NEEDS_RESEARCH
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_NEEDS_RESEARCH = getattr(app_class, "_answer_needs_research", None)

    if callable(_ORIGINAL_NEEDS_RESEARCH):
        def needs_research(answer: str) -> bool:
            if not str(answer or "").strip():
                return True
            return bool(_ORIGINAL_NEEDS_RESEARCH(answer))
        app_class._answer_needs_research = staticmethod(needs_research)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None, **kwargs) -> dict[str, Any]:
        result = dict(_ORIGINAL_CHAT(self, message, owner_id=owner_id, session_id=session_id, **kwargs))
        if not str(result.get("answer") or "").strip() and not bool(result.get("stopped")):
            result["answer"] = _FALLBACK
            _repair_saved_empty(self, str(result.get("session_id") or session_id or ""), _FALLBACK)
            event("honest_uncertainty_fallback", channel="chat", session_id=str(result.get("session_id") or ""))
        return result

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        parts: list[str] = []
        had_error = False
        for item in _ORIGINAL_STREAM(
            self,
            message,
            owner_id=owner_id,
            session_id=session_id,
            request_id=request_id,
        ):
            if item.type == "delta":
                value = str(item.data.get("text") or "")
                if value:
                    parts.append(value)
                yield item
                continue
            if item.type == "error":
                had_error = True
                yield item
                continue
            if item.type != "done":
                yield item
                continue

            payload = dict(item.data)
            if bool(payload.get("stopped")) or had_error:
                yield item
                continue
            answer = str(payload.get("answer") or "").strip()
            streamed = "".join(parts).strip()
            if not answer and streamed:
                payload["answer"] = streamed
                yield StreamEvent("done", payload)
                continue
            if not answer and not streamed:
                sid = str(payload.get("session_id") or session_id or "")
                _repair_saved_empty(self.app, sid, _FALLBACK)
                yield StreamEvent("delta", {"text": _FALLBACK})
                payload["answer"] = _FALLBACK
                event("honest_uncertainty_fallback", channel="stream", session_id=sid)
                yield StreamEvent("done", payload)
                continue
            yield item

    app_class.chat = chat
    RealtimeChatManager.stream = stream
    event("honest_uncertainty_runtime_installed")


__all__ = ["install_honest_uncertainty"]
