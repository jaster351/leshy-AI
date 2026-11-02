from __future__ import annotations

from typing import Iterator

from .inference_scheduler import get_inference_scheduler
from .realtime_chat import RealtimeChatManager, StreamEvent


_INSTALLED = False
_ORIGINAL_STREAM = None


def install_chat_activity_runtime() -> None:
    """Показывать пользователю, чем занят интерактивный чат, не меняя transport.

    Эталонный Web transport остаётся владельцем SSE. Этот слой только добавляет
    activity-событие перед существующим stream, чтобы ожидание занятой модели не
    выглядело как зависание.
    """
    global _INSTALLED, _ORIGINAL_STREAM
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_STREAM = RealtimeChatManager.stream

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        owner = str(owner_id or "web:local")
        scheduler = get_inference_scheduler(self.app)
        status = scheduler.status()
        active = status.get("active") or {}
        active_source = str(active.get("source") or "")
        if active_source and active_source != owner:
            label = "другой запрос"
            if active_source.startswith("telegram:"):
                label = "Telegram"
            elif active_source.startswith("web:"):
                label = "другой Web-чат"
            elif active_source == "startup-recovery":
                label = "восстановление после запуска"
            elif "background" in active_source:
                label = "фоновая задача"
            yield StreamEvent("activity", {
                "kind": "inference_queue",
                "phase": "waiting",
                "session_id": str(session_id or ""),
                "text": f"Жду освобождения модели · сейчас выполняется: {label}",
                "active_source": active_source,
            })
        else:
            yield StreamEvent("activity", {
                "kind": "generation",
                "phase": "preparing",
                "session_id": str(session_id or ""),
                "text": "Готовлю ответ…",
            })
        yield from _ORIGINAL_STREAM(
            self,
            message,
            owner_id=owner_id,
            session_id=session_id,
            request_id=request_id,
        )

    RealtimeChatManager.stream = stream


__all__ = ["install_chat_activity_runtime"]
