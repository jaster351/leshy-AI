from __future__ import annotations

import re
from typing import Any, Iterator

from .diagnostics import event, problem
from .realtime_chat import RealtimeChatManager, StreamEvent
from .telegram_fast import FastTelegramBridge


_INSTALLED = False
_ORIGINAL_STREAM = None
_ORIGINAL_CHAT = None
_ORIGINAL_TG_HANDLE = None
_ORIGINAL_TG_STATUS = None


def _identity(app):
    return getattr(app, "owner_identity", None)


def canonical_owner(app, owner_id: str) -> str:
    value = str(owner_id or "").strip()[:200] or "web:local"
    service = _identity(app)
    if service is None:
        return value
    if value.startswith("telegram:"):
        telegram_user_id = value.split(":", 1)[1].strip()
        if telegram_user_id:
            try:
                return service.resolve_telegram(telegram_user_id)
            except Exception as exc:
                problem("owner_identity_telegram_resolve_failed", str(exc), telegram_user_id=telegram_user_id)
                return value
    # Web идентифицируется серверной HttpOnly-сессией, а подтверждённый UUID
    # после bootstrap передаётся совместимым старому UI owner_id. Нельзя глобально
    # превращать web:local в первый профиль: другой браузер до привязки должен
    # оставаться отдельным пользователем.
    return value


def _link_code(text: str) -> str:
    value = str(text or "").strip()
    match = re.match(r"(?i)^/start\s+link[_-]([A-Z0-9-]{6,20})\s*$", value)
    if not match:
        match = re.match(r"(?i)^/link\s+([A-Z0-9-]{6,20})\s*$", value)
    return re.sub(r"[^A-Z0-9]", "", match.group(1).upper()) if match else ""


def _wrap_owner_sensitive_resolvers(app) -> None:
    """Канонизировать owner_id в подтверждениях, которые идут мимо chat()."""
    controller = getattr(app, "os_control", None)
    if controller is None or getattr(controller, "_leshy_owner_identity_wrapped", False):
        return
    original_resolve = getattr(controller, "resolve", None)
    if callable(original_resolve):
        def resolve(approval_id, *, owner_id: str, allow: bool, **kwargs):
            return original_resolve(
                approval_id,
                owner_id=canonical_owner(app, owner_id),
                allow=allow,
                **kwargs,
            )
        controller.resolve = resolve
    original_pending = getattr(controller, "pending", None)
    if callable(original_pending):
        def pending(*, owner_id: str, session_id: str | None = None, **kwargs):
            return original_pending(
                owner_id=canonical_owner(app, owner_id),
                session_id=session_id,
                **kwargs,
            )
        controller.pending = pending
    controller._leshy_owner_identity_wrapped = True


def install_owner_identity_runtime(app_class, app, telegram: FastTelegramBridge | None = None) -> None:
    """Подключить единую идентичность без требования Web-авторизации для Telegram.

    Любой Telegram user_id может общаться с Лешим. Пока пользователь не связал
    Telegram с Web-профилем, owner_id остаётся ``telegram:<id>``. После
    подтверждения одноразового кода тот же канал начинает использовать общий
    owner UUID. Привилегированные системные команды по-прежнему проверяют
    Telegram ID хозяина существующей политикой TelegramBridge._is_owner().
    """
    global _INSTALLED, _ORIGINAL_STREAM, _ORIGINAL_CHAT, _ORIGINAL_TG_HANDLE, _ORIGINAL_TG_STATUS
    _wrap_owner_sensitive_resolvers(app)
    if _INSTALLED:
        if telegram is not None:
            telegram.owner_identity = getattr(app, "owner_identity", None)
        return
    _INSTALLED = True

    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_TG_HANDLE = FastTelegramBridge._handle_message
    _ORIGINAL_TG_STATUS = FastTelegramBridge.status

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        owner = canonical_owner(self.app, owner_id)
        yield from _ORIGINAL_STREAM(
            self,
            message,
            owner_id=owner,
            session_id=session_id,
            request_id=request_id,
        )

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None, **kwargs) -> dict[str, Any]:
        owner = canonical_owner(self, owner_id)
        return _ORIGINAL_CHAT(self, message, owner_id=owner, session_id=session_id, **kwargs)

    def can_chat(self: FastTelegramBridge, user_id: str) -> bool:
        # Разговорный доступ открыт. Это НЕ даёт права на owner-only команды,
        # управление ОС или административные действия.
        return bool(str(user_id or "").strip())

    def status(self: FastTelegramBridge) -> dict:
        value = _ORIGINAL_TG_STATUS(self)
        value.update({
            "open_chat_for_all": True,
            "owner_identity_linking": True,
            "unlinked_users_have_isolated_sessions": True,
        })
        return value

    def handle_message(self: FastTelegramBridge, message: dict) -> None:
        sender = message.get("from") or {}
        text = str(message.get("text") or message.get("caption") or "").strip()
        user_id = str(sender.get("id") or "").strip()
        chat_id = (message.get("chat") or {}).get("id")
        code = _link_code(text)
        if code and user_id and chat_id is not None:
            service = getattr(self, "owner_identity", None) or _identity(self.app)
            if service is None:
                self._send(chat_id, "Сервис профиля Лешего ещё не готов. Повторите привязку после перезапуска.")
                return
            try:
                result = service.consume_telegram_link(
                    code,
                    user_id,
                    username=str(sender.get("username") or ""),
                    first_name=str(sender.get("first_name") or ""),
                    last_name=str(sender.get("last_name") or ""),
                )
                if result.get("mode") == "login":
                    self._send(chat_id, "Готово. Этот Web-браузер вошёл в уже привязанный к Вам профиль Лешего.")
                else:
                    self._send(chat_id, "Готово. Telegram привязан к Вашему Web-профилю Лешего. Теперь память и подключённые сервисы могут использовать единый профиль.")
                event("telegram_owner_identity_link_handled", telegram_user_id=user_id, mode=str(result.get("mode") or ""))
            except Exception as exc:
                self._send(chat_id, f"Не удалось привязать профиль: {exc}")
                problem("telegram_owner_identity_link_failed", str(exc), telegram_user_id=user_id)
            return
        return _ORIGINAL_TG_HANDLE(self, message)

    RealtimeChatManager.stream = stream
    app_class.chat = chat
    FastTelegramBridge._can_chat = can_chat
    FastTelegramBridge.status = status
    FastTelegramBridge._handle_message = handle_message

    if telegram is not None:
        telegram.owner_identity = getattr(app, "owner_identity", None)


__all__ = ["canonical_owner", "install_owner_identity_runtime"]
