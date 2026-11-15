"""Telegram identity confirmation for Web clients on LAN/IP behind NAT."""
from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from . import web_server_impl16 as previous
from .diagnostics import problem
from .telegram_browser_login import TelegramBrowserLogin
from .telegram_browser_login_runtime import install_telegram_browser_login_runtime
from .constitution_runtime import install_constitution_routing
from .semantic_research_router import install_semantic_research_router
from .research_tuning import store_for
from .role_authority import creator_telegram_id, register_local_creator, role_for_web, ROLE_DISPLAY


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    identity = app.owner_identity
    login = getattr(app, "telegram_browser_login", None)
    if not isinstance(login, TelegramBrowserLogin):
        login = TelegramBrowserLogin(identity)
        app.telegram_browser_login = login
    install_telegram_browser_login_runtime()
    # Apply after the other Web/Telegram runtime wrappers; use the verified
    # local Constitution file without web search or an LLM-generated path.
    install_constitution_routing(type(app))
    install_semantic_research_router()

    class Handler(Parent):
        def _identity_status_payload(self, owner_id: str) -> dict:
            # The verified owner is read from the server-side Web cookie. Repair
            # earlier Telegram conversations before telling the UI to reload.
            try:
                login.reconcile_linked_telegram_history(owner_id)
            except Exception as exc:
                problem("telegram_web_history_reconcile_status_failed",
                        type(exc).__name__)
            payload = super()._identity_status_payload(owner_id)
            current_role = role_for_web(telegram, identity, owner_id)
            creator_id = creator_telegram_id(telegram)
            from_loopback = str(self.client_address[0]) in {"127.0.0.1", "::1"}
            payload.update({
                "verified_role": current_role,
                "verified_role_label": ROLE_DISPLAY.get(current_role, "пользователь"),
                "creator_configured": bool(creator_id),
                "creator_bootstrap_available": bool(
                    not creator_id and from_loopback and payload.get("telegram_linked")
                ),
                "server_owner": current_role in {"creator", "server_owner"} and
                    bool(telegram._is_owner(next((
                        str(item.get("user_id") or "")
                        for item in payload.get("telegram", [])
                        if telegram._is_owner(str(item.get("user_id") or ""))
                    ), ""))),
            })
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

        def _research_owner_allowed(self) -> bool:
            if not self._authorized() or not self._existing_web_owner():
                self._identity_json(HTTPStatus.UNAUTHORIZED, {
                    "error": "Требуется подтверждённая Web-сессия",
                })
                return False
            role = role_for_web(telegram, identity, self._existing_web_owner())
            if role not in {"creator", "server_owner"}:
                self._identity_json(HTTPStatus.FORBIDDEN, {
                    "error": "Настройки интернет-поиска доступны только хозяину этого сервера",
                })
                return False
            return True

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/research-settings-panel.js":
                try:
                    return self._send_asset(
                        "research-settings-panel.js", "application/javascript; charset=utf-8",
                    )
                except FileNotFoundError:
                    return self._identity_json(HTTPStatus.NOT_FOUND, {"error": "Панель поиска не найдена"})
            if route == "/v1/research/settings":
                if not self._research_owner_allowed():
                    return
                return self._identity_json(HTTPStatus.OK, store_for(app).status())
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
            if route in {"/v1/sessions", "/v1/history", "/v1/memories"}:
                owner = self._existing_web_owner()
                if owner:
                    try:
                        login.reconcile_linked_telegram_history(owner)
                    except Exception as exc:
                        problem("telegram_web_history_reconcile_list_failed",
                                type(exc).__name__)
            return super().do_GET()

        def do_POST(self):
            route = urlsplit(self.path).path
            if route == "/v1/research/settings":
                if not self._research_owner_allowed() or not self._identity_write_allowed():
                    return
                try:
                    body = self._body()
                    store_for(app).save(
                        mode=str(body.get("mode", "auto")),
                        level=body.get("level", "5.00"),
                    )
                    return self._identity_json(HTTPStatus.OK, store_for(app).status())
                except (ValueError, TypeError) as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except OSError:
                    return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось сохранить настройки интернет-поиска",
                    })
            if route == "/v1/identity/creator/claim":
                # One-time explicit bootstrap on the actual local computer,
                # never from LAN/internet, Telegram text, model, or a guest.
                if not self._identity_write_allowed():
                    return
                if str(self.client_address[0]) not in {"127.0.0.1", "::1"}:
                    return self._identity_json(HTTPStatus.FORBIDDEN, {
                        "error": "Первичная регистрация Создателя доступна только на самом компьютере Лешего (localhost)",
                    })
                owner = self._existing_web_owner()
                try:
                    body = self._body()
                    if body.get("confirm") != "I_AM_CREATOR":
                        raise ValueError("Не подтверждено назначение Создателя")
                    profile = identity.status(owner)
                    linked = [
                        str(row.get("user_id") or "").strip()
                        for row in profile.get("telegram", [])
                    ]
                    if len(linked) != 1 or not linked[0].isdecimal():
                        raise ValueError("Сначала подтвердите вход в Web через свой Telegram в личном чате бота")
                    result = register_local_creator(telegram, linked[0])
                    return self._identity_json(HTTPStatus.OK, result)
                except (ValueError, PermissionError) as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("creator_role_registration_failed", type(exc).__name__)
                    return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось сохранить роль Создателя на локальном узле",
                    })
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
