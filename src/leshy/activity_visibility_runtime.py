from __future__ import annotations

from typing import Iterator

from .diagnostics import event, problem
from .realtime_chat import RealtimeChatManager, StreamEvent


_INSTALLED = False
_ORIGINAL_STREAM = None


def _telegram_chat_id(session_id: str | None) -> int | None:
    value = str(session_id or "")
    if not value.startswith("telegram:"):
        return None
    parts = value.split(":", 2)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except Exception:
        return None


def install_activity_visibility(app=None) -> None:
    """Делать activity-события видимыми в Telegram без изменения Web transport.

    Web уже получает activity через SSE. Для Telegram раньше эти события
    игнорировались, поэтому пользователь видел только typing. Этот слой создаёт
    одно временное статус-сообщение, обновляет его по этапам и удаляет перед
    первым реальным токеном/финальным ответом.
    """
    global _INSTALLED, _ORIGINAL_STREAM
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_STREAM = RealtimeChatManager.stream

    def stream(self: RealtimeChatManager, message: str, *, owner_id: str = "web:local", session_id: str | None = None, request_id: str | None = None) -> Iterator[StreamEvent]:
        telegram_mode = str(owner_id or "").startswith("telegram:")
        bridge = getattr(self.app, "telegram", None) if telegram_mode else None
        chat_id = _telegram_chat_id(session_id) if telegram_mode else None
        status_message_id = 0

        def clear_status() -> None:
            nonlocal status_message_id
            if not status_message_id or bridge is None or chat_id is None:
                return
            try:
                bridge._api("deleteMessage", {"chat_id": chat_id, "message_id": int(status_message_id)}, timeout=5)
            except Exception:
                pass
            status_message_id = 0

        try:
            for item in _ORIGINAL_STREAM(
                self,
                message,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            ):
                if telegram_mode and item.type == "activity" and bridge is not None and chat_id is not None:
                    text = str(item.data.get("text") or "Леший работает…").strip()
                    if text:
                        try:
                            if status_message_id:
                                bridge._api(
                                    "editMessageText",
                                    {"chat_id": chat_id, "message_id": int(status_message_id), "text": text},
                                    timeout=5,
                                )
                            else:
                                result = bridge._api("sendMessage", {"chat_id": chat_id, "text": text}, timeout=5) or {}
                                status_message_id = int(result.get("message_id") or 0) if isinstance(result, dict) else 0
                            event("telegram_activity_visible", chat_id=str(chat_id), text=text[:200])
                        except Exception as exc:
                            problem("telegram_activity_visible_failed", str(exc), chat_id=str(chat_id))
                elif telegram_mode and item.type in {"delta", "done", "error"}:
                    clear_status()
                yield item
        finally:
            clear_status()

    RealtimeChatManager.stream = stream
    event("activity_visibility_runtime_installed", telegram_status_messages=True)


__all__ = ["install_activity_visibility"]
