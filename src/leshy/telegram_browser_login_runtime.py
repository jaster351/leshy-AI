"""Telegram deep-link and inline-button login bridge.

Runs inside the existing Telegram polling process. No public callback, webhook,
external domain or OAuth server is needed.
"""
from __future__ import annotations

import re

from .diagnostics import event, problem
from .telegram_fast import FastTelegramBridge


_INSTALLED = False
_PREVIOUS_HANDLE_MESSAGE = None
_PREVIOUS_HANDLE_CALLBACK = None

_START = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?\s+auth_([A-Za-z0-9_-]{30,60})\s*$", re.I)
_CONFIRM = re.compile(r"^tga:(y|n):([A-Za-z0-9_-]{20,45})$")


def install_telegram_browser_login_runtime() -> None:
    global _INSTALLED, _PREVIOUS_HANDLE_MESSAGE, _PREVIOUS_HANDLE_CALLBACK
    if _INSTALLED:
        return
    _INSTALLED = True
    _PREVIOUS_HANDLE_MESSAGE = FastTelegramBridge._handle_message
    _PREVIOUS_HANDLE_CALLBACK = FastTelegramBridge._handle_callback_query

    def handle_message(self, message: dict) -> None:
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        sender_id = str(sender.get("id") or "")
        chat_id = str(chat.get("id") or "")
        match = _START.fullmatch(text)
        login = getattr(self.app, "telegram_browser_login", None)
        if match:
            if not (login and sender_id.isdecimal() and chat.get("type") == "private"
                    and sender_id == chat_id):
                if chat_id:
                    self._send(chat_id, "Вход в Лешего подтверждается только в личном чате.")
                return
            try:
                info = login.prepare_telegram_confirmation(match.group(1), sender_id)
                prompt = (
                    "Вход в веб-профиль Лешего\n"
                    "Сверьте код на экране компьютера: " + info["short_code"] + "\n\n"
                    "Подтверждайте только если Вы сами открыли вход в браузере. "
                    "Разрешение даст этому браузеру доступ к Вашей истории Лешего. "
                    "Вход в аккаунт не даёт автоматически права администратора."
                )
                self._api("sendMessage", {
                    "chat_id": int(sender_id),
                    "text": prompt,
                    "reply_markup": {"inline_keyboard": [[
                        {"text": "✅ Подтвердить вход",
                         "callback_data": "tga:y:" + info["callback"]},
                        {"text": "❌ Отменить",
                         "callback_data": "tga:n:" + info["callback"]},
                    ]]},
                }, timeout=10)
                event("telegram_browser_login_confirmation_requested",
                      telegram_user_id=sender_id)
            except (ValueError, PermissionError) as exc:
                self._send(chat_id, str(exc))
            except Exception as exc:
                problem("telegram_browser_login_prompt_failed", type(exc).__name__)
                self._send(chat_id, "Не удалось показать подтверждение. Попробуйте снова.")
            return

        result = _PREVIOUS_HANDLE_MESSAGE(self, message)
        # Legacy /link confirmation also gains Telegram name and photo.
        if login and sender_id.isdecimal() and chat.get("type") == "private" and (
            text.lower().startswith("/link ") or text.lower().startswith("/start link_")
        ):
            try:
                login.sync_verified_sender(sender_id, sender)
                login.sync_verified_telegram_profile(self, sender_id)
            except Exception as exc:
                problem("telegram_browser_login_legacy_profile_sync_failed",
                        type(exc).__name__)
        return result

    def handle_callback(self, query: dict) -> None:
        data = str(query.get("data") or "")
        match = _CONFIRM.fullmatch(data)
        if not match:
            return _PREVIOUS_HANDLE_CALLBACK(self, query)
        callback_id = str(query.get("id") or "")
        sender = query.get("from") or {}
        sender_id = str(sender.get("id") or "")
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        login = getattr(self.app, "telegram_browser_login", None)
        answer = "Подтверждение недоступно"
        approved = match.group(1) == "y"
        if not (login and sender_id.isdecimal()
                and chat.get("type") == "private"
                and str(chat.get("id") or "") == sender_id):
            answer = "Вход подтверждается только в личном чате."
        else:
            try:
                result = login.finish(match.group(2), sender_id, sender, approved)
                answer = ("Вход подтверждён. Вернитесь в браузер Лешего."
                          if approved else "Вход отменён.")
                event("telegram_browser_login_button_resolved", outcome=result["mode"])
            except (ValueError, PermissionError) as exc:
                answer = str(exc)
            except Exception as exc:
                problem("telegram_browser_login_confirmation_failed", type(exc).__name__)
                answer = "Не удалось подтвердить вход."
        if callback_id:
            try:
                self._api("answerCallbackQuery", {
                    "callback_query_id": callback_id,
                    "text": answer[:180], "show_alert": not approved,
                }, timeout=5)
            except Exception:
                pass
        if chat.get("id") is not None and message.get("message_id"):
            try:
                self._api("editMessageReplyMarkup", {
                    "chat_id": chat["id"], "message_id": int(message["message_id"]),
                    "reply_markup": {"inline_keyboard": []},
                }, timeout=5)
            except Exception:
                pass
        if approved and answer.startswith("Вход подтверждён") and login:
            login.sync_verified_telegram_profile(self, sender_id)

    FastTelegramBridge._handle_message = handle_message
    FastTelegramBridge._handle_callback_query = handle_callback


__all__ = ["install_telegram_browser_login_runtime"]
