"""Потокобезопасный счётчик незавершённых ответов для обновления Лешего."""
from __future__ import annotations

from functools import wraps
import threading
import time


class ConversationActivity:
    """Счётчик без текста сообщений, идентификаторов пользователей и секретов."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active = 0
        self._idle_since = time.monotonic()

    def begin(self) -> None:
        with self._lock:
            self._active += 1
            self._idle_since = time.monotonic()

    def end(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("Несогласованный счётчик активных ответов")
            self._active -= 1
            if self._active == 0:
                self._idle_since = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active_conversations": self._active,
                "idle_seconds": round(max(0.0, time.monotonic() - self._idle_since), 2)
                if self._active == 0 else 0.0,
            }


def tracked_chat(method):
    """Включает синхронные ответы Telegram/HTTP в общий счётчик."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        tracker = getattr(self, "conversation_activity", None)
        if tracker is None:
            return method(self, *args, **kwargs)
        tracker.begin()
        try:
            return method(self, *args, **kwargs)
        finally:
            tracker.end()

    return wrapper
