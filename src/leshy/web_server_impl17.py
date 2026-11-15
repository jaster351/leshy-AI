"""Telegram identity confirmation for Web clients on LAN/IP behind NAT."""
from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from . import web_server_impl16 as previous
from .diagnostics import problem
from .telegram_browser_login import TelegramBrowserLogin
from .telegram_browser_login_runtime import install_telegram_browser_login_runtime


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    identity = app.owner_identity
    login = getattr(app, "telegram_browser_login", None)
    if not isinstance(login, TelegramBrowserLogin):
        login = TelegramBrowserLogin(identity)
        app.telegram_browser_login = login
    install_telegram_browser_login_runtime()

    class Handler(Parent):
        def _identity_status_payload(self, owner_id: str) -> dict:
            payload = super()._identity_status_payload(owner_id)
            payload["profile"] = login.profile(
                owner_id, payload.get("avatar") or {},
            )
            # The Telegram callback acknowledges login before Bot API photo
            # retrieval finishes. Do not wait or force the user to log in again:
            # retry the missing photo in a bounded background task.
            if (payload["profile"].get("provider") == "telegram"
                    and payload["profile"].get("photo_pending")):
                login.ensure_photo_refresh(telegram, owner_id)
            payload["telegram_login_button_available"] = bool(
                str(payload.get("telegram_bot_username") or "").strip()
            )
            return payload

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/v1/identity/telegram/login/status":
                if not self._authorized() or not self._existing_web_owner():
                    return self._identity_json(HTTPStatus.UNAUTHORIZED, {
                        "error": "Требуется действующий гостевой Web-профиль",
                    })
                cookie = self._cookie_value(identity.cookie_name)
                return self._identity_json(
                    HTTPStatus.OK,
                    login.status(identity._hash_secret(cookie)),
                )
            if route == "/v1/identity/telegram/avatar":
                # <img> sends the HttpOnly same-site cookie but not the
                # Authorization: Bearer header used by JSON fetch requests.
                # A valid, unexpired server-side Web session is sufficient and
                # required; never accept owner_id from URL/JS for this image.
                owner = self._existing_web_owner()
                if not owner:
                    return self._identity_json(HTTPStatus.UNAUTHORIZED, {
                        "error": "Отсутствует Web-профиль",
                    })
                result = login.avatar(telegram, owner)
                if not result:
                    return self._identity_json(HTTPStatus.NOT_FOUND, {
                        "error": "Фотография Telegram пока недоступна",
                    })
                raw, mime = result
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("Vary", "Cookie")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(raw)
                return
            return super().do_GET()

        def do_POST(self):
            route = urlsplit(self.path).path
            if route != "/v1/identity/telegram/login/start":
                return super().do_POST()
            if not self._identity_write_allowed():
                return
            try:
                bot_status = telegram.status()
                bot_username = str(bot_status.get("bot_username") or "").strip().lstrip("@")
                if not bot_username:
                    return self._identity_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "error": "Бот Telegram не настроен или его имя не определено",
                    })
                session_hash = identity._hash_secret(
                    self._cookie_value(identity.cookie_name)
                )
                token = login.begin(session_hash)
                # Deep link uses Telegram's official t.me domain. Neither the
                # Web server nor local PC needs a publicly reachable address.
                return self._identity_json(HTTPStatus.OK, {
                    "deep_link": f"https://t.me/{bot_username}?start=auth_{token['token']}",
                    "short_code": token["short_code"],
                    "expires_in": token["expires_in"],
                })
            except (ValueError, PermissionError) as exc:
                return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                problem("telegram_browser_login_start_failed", type(exc).__name__)
                return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": "Не удалось создать запрос входа",
                })

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    original = previous.make_handler
    previous.make_handler = make_handler
    try:
        previous.serve(host=host, port=port)
    finally:
        previous.make_handler = original


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
