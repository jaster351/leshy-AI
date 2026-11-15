from __future__ import annotations

from .diagnostics import event, problem
from .telegram_fast import FastTelegramBridge
from .telegram_rich import telegram_html_chunks


_INSTALLED = False


def finalize_answer_exact_once(
    bridge: FastTelegramBridge,
    chat_id,
    telegram_message_id: int,
    answer: str,
    reply_to_message_id: int | None,
    *,
    approval: dict | None = None,
) -> int:
    """Финализировать streaming-ответ без двусмысленного повторного sendMessage.

    Telegram API не даёт идемпотентный ключ для sendMessage/editMessageText. Если
    сеть оборвалась после того, как сервер Telegram применил edit, клиент может
    получить исключение и ошибочно решить, что edit не состоялся. Старый fallback
    в этом случае отправлял полный ответ вторым сообщением. Поэтому после появления
    preview мы только повторяем edit; при исчерпании retry сохраняем существующее
    сообщение и логируем проблему, но не создаём возможный дубль.
    """
    chunks = telegram_html_chunks(answer)
    if not chunks:
        chunks = ["…"]
    keyboard = bridge._approval_keyboard(approval)
    first_id = int(telegram_message_id or 0)

    if first_id:
        edited = False
        for delay in (0.0,) + tuple(bridge.final_retry_delays):
            if delay > 0 and bridge._stop.wait(delay):
                break
            if bridge._edit_stream_message(
                chat_id,
                first_id,
                chunks[0],
                html_mode=True,
                reply_markup=keyboard,
                final=True,
            ):
                edited = True
                break
        if not edited:
            # Критично: НЕ делаем sendMessage(chunks[0]). Нельзя отличить
            # «Telegram применил edit, но HTTP-ответ потерян» от настоящего отказа.
            problem(
                "telegram_final_edit_unconfirmed",
                "Финальный edit не подтверждён; повторный sendMessage подавлен для защиты от дубля",
                chat_id=str(chat_id),
                telegram_message_id=first_id,
                answer_chars=len(str(answer or "")),
            )
            event(
                "telegram_duplicate_fallback_suppressed",
                chat_id=str(chat_id),
                telegram_message_id=first_id,
            )
    else:
        payload = {"chat_id": chat_id, "text": chunks[0], "parse_mode": "HTML"}
        if reply_to_message_id:
            payload["reply_parameters"] = {
                "message_id": int(reply_to_message_id),
                "allow_sending_without_reply": True,
            }
        if keyboard:
            payload["reply_markup"] = keyboard
        result = bridge._send_with_retry("sendMessage", payload, timeout=10) or {}
        first_id = int(result.get("message_id") or 0) if isinstance(result, dict) else 0

    # Последующие chunks являются продолжением ответа, а не дублем первого.
    # Каждый отправляется ровно один раз через существующую retry-политику.
    for rendered in chunks[1:]:
        bridge._send_with_retry(
            "sendMessage",
            {"chat_id": chat_id, "text": rendered, "parse_mode": "HTML"},
            timeout=10,
        )
    return first_id


def install_telegram_exact_once_finalization() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    def finalize(self, chat_id, telegram_message_id, answer, reply_to_message_id, *, approval=None):
        return finalize_answer_exact_once(
            self,
            chat_id,
            telegram_message_id,
            answer,
            reply_to_message_id,
            approval=approval,
        )

    FastTelegramBridge._finalize_answer = finalize
    event("telegram_exact_once_finalization_installed")


__all__ = ["finalize_answer_exact_once", "install_telegram_exact_once_finalization"]
