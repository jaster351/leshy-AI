from __future__ import annotations

import json
from urllib import error

from .telegram import TelegramBridge


_INSTALLED = False
_ORIGINAL_API = TelegramBridge._api


def install_telegram_http_error_details() -> None:
    """Не терять description Telegram при HTTP 4xx.

    urllib выбрасывает HTTPError до разбора JSON-тела Bot API. Из-за этого код
    видел только ``HTTP Error 400: Bad Request`` и не мог отличить безопасный
    ``message is not modified`` от настоящей ошибки разметки/сообщения.
    Токен и payload в исключение не попадают.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    def api(self, method: str, payload: dict | None = None, timeout: float = 20):
        try:
            return _ORIGINAL_API(self, method, payload, timeout)
        except error.HTTPError as exc:
            description = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                body = json.loads(raw)
                if isinstance(body, dict):
                    description = str(body.get("description") or "").strip()
            except Exception:
                description = ""
            if description:
                raise RuntimeError(f"Telegram API HTTP {int(exc.code)}: {description}") from exc
            raise

    TelegramBridge._api = api


__all__ = ["install_telegram_http_error_details"]
