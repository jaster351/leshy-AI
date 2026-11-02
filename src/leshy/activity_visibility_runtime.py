from __future__ import annotations

from typing import Iterator

from .diagnostics import event
from .realtime_chat import RealtimeChatManager, StreamEvent


_INSTALLED = False
_ORIGINAL_STREAM = None


def install_activity_visibility(app=None) -> None:
    """Показывать текущую работу Лешего без дополнительного Telegram API roundtrip.

    Web уже получает `activity` через SSE. Telegram FastBridge умеет показывать
    обычные `delta`, поэтому для новых типов activity превращаем служебный этап
    во временный preview текущего ответа. Freshness-события не трогаем: старый
    `_TelegramFreshnessProxy` уже обрабатывает их и гарантирует чистый финальный
    ответ без служебного текста.
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
        telegram_mode = str(owner_id or "").startswith("telegram:")
        freshness_active = False
        for item in _ORIGINAL_STREAM(
            self,
            message,
            owner_id=owner_id,
            session_id=session_id,
            request_id=request_id,
        ):
            if not telegram_mode or item.type != "activity":
                yield item
                continue

            kind = str(item.data.get("kind") or "")
            phase = str(item.data.get("phase") or "")
            text = str(item.data.get("text") or "Леший работает…").strip()

            # Этот контракт уже реализован freshness proxy. Оставляем событие
            # нетронутым, чтобы не получить два preview и не смешать служебный
            # текст с итоговым ответом.
            if kind == "freshness":
                freshness_active = True
                yield item
                continue
            if freshness_active and kind == "web_search":
                yield item
                continue

            # Остальные этапы (в первую очередь Browser Skills) показываются
            # через существующий streaming preview Telegram. Никаких отдельных
            # HTTP-запросов к Telegram API здесь нет.
            if text:
                event(
                    "telegram_activity_preview",
                    owner_id=str(owner_id)[:160],
                    kind=kind,
                    phase=phase,
                    text=text[:200],
                )
                yield StreamEvent("delta", {"text": text + "\n\n", "activity_preview": True})
            else:
                yield item

    RealtimeChatManager.stream = stream
    event("activity_visibility_runtime_installed", telegram_via_existing_stream=True)


__all__ = ["install_activity_visibility"]
