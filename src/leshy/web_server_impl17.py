"""Telegram identity confirmation for Web clients on LAN/IP behind NAT."""
from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from . import web_server_impl16 as previous
from .diagnostics import problem
from .telegram_browser_login import TelegramBrowserLogin
from .telegram_browser_login_runtime import install_telegram_browser_login_runtime
from .constitution_runtime import install_constitution_routing
from .semantic_research_router import install_semantic_research_router
from .owner_knowledge_capture import install_owner_document_routing
from .research_tuning import store_for
from .model_repository_search import search_hub_models
from .module_settings import module_enabled
from .superagent_ssh_pty import InteractiveSSHBroker
from .role_authority import creator_telegram_id, register_local_creator, role_for_web, ROLE_DISPLAY
from .mentor_view import mentor_snapshot
from .mentor_delivery_request import request_delivery, request_state
from pathlib import Path
from .android_voice_access import authorized_owner as _android_voice_owner, conversational_answer as _android_voice_answer


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
    # Last route: local Knowledge saves must never become browser/parcel/IP jobs.
    install_owner_document_routing(type(app))

    pty_broker = getattr(app, "_ssh_pty_broker", None)
    if pty_broker is None:
        pty_broker = InteractiveSSHBroker()
        app._ssh_pty_broker = pty_broker
        previous_stop = getattr(app, "stop_background", None)
        if callable(previous_stop):
            def stop_with_ssh_pty():
                pty_broker.shutdown()
                return previous_stop()
            app.stop_background = stop_with_ssh_pty

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
                "creator_bootstrap_available": False,
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

        def _mentor_delivery_owner(self):
            owner = self._existing_web_owner()
            if not owner or role_for_web(telegram, identity, owner) != "creator":
                return False
            try:
                return any(telegram._is_owner(str(row.get("user_id") or ""))
                           for row in identity.status(owner).get("telegram", []))
            except (OSError, ValueError, TypeError, KeyError):
                return False

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/v1/mentor/history":
                owner = self._existing_web_owner()
                if not self._authorized() or not owner:
                    return self._identity_json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется подтверждённая Web-сессия"})
                if role_for_web(telegram, identity, owner) != "creator":
                    return self._identity_json(HTTPStatus.FORBIDDEN, {"error": "Доступ только для Создателя"})
                try:
                    payload = mentor_snapshot(settings.brain_dir)
                    payload['delivery_request'] = request_state(Path(settings.brain_dir) / 'ai-mentor')
                    payload['delivery_allowed'] = self._mentor_delivery_owner()
                    return self._identity_json(HTTPStatus.OK, payload)
                except (OSError, ValueError, TypeError):
                    return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось прочитать журнал AI-mentor"})
            if route == "/v1/android/voice/status":
                owner = _android_voice_owner(settings.brain_dir, self.headers.get("Authorization", ""))
                if not owner:
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Android voice key missing or invalid"})
                return self._json(HTTPStatus.OK, {"ready": True, "mode": "conversation_only"})
            if route == "/v1/model-repositories":
                if not self._research_owner_allowed():
                    return
                values = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                def param(name, default=""):
                    return str((values.get(name) or [default])[0])
                try:
                    results = search_hub_models(
                        name=param("name"), author=param("author"),
                        specialty=param("specialty", "all"),
                        date_from=param("from"), date_to=param("to"),
                        limit=param("limit", "30"),
                    )
                    return self._identity_json(HTTPStatus.OK, results)
                except (ValueError, TypeError) as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {
                        "error": str(exc)[:250],
                    })
                except Exception as exc:
                    problem("model_repository_search_failed", type(exc).__name__)
                    return self._identity_json(HTTPStatus.BAD_GATEWAY, {
                        "error": "Не удалось получить каталог моделей Hugging Face",
                    })
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
            if route == "/v1/mentor/delivery":
                if not self._identity_write_allowed():
                    return
                if not self._mentor_delivery_owner():
                    return self._identity_json(HTTPStatus.FORBIDDEN, {"error": "Требуется подтверждённый Создатель и хозяин сервера"})
                try:
                    if self._body() != {}:
                        return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": "Ожидается пустой JSON-объект"})
                    return self._identity_json(HTTPStatus.ACCEPTED, request_delivery(settings.brain_dir))
                except ValueError as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:200]})
                except (OSError, TypeError):
                    return self._identity_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Не удалось поставить запрос доставки"})
            if route == "/v1/android/voice/chat":
                owner = _android_voice_owner(settings.brain_dir, self.headers.get("Authorization", ""))
                if not owner:
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Android voice key missing or invalid"})
                try:
                    return self._json(HTTPStatus.OK, _android_voice_answer(app, owner, self._body()))
                except (ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:250]})
                except Exception as exc:
                    problem("android_voice_chat_failed", type(exc).__name__)
                    return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Voice model temporarily unavailable"})
            if route in {
                "/v1/superagent/ssh/pty/open",
                "/v1/superagent/ssh/pty/read",
                "/v1/superagent/ssh/pty/prepare",
                "/v1/superagent/ssh/pty/write",
                "/v1/superagent/ssh/pty/close",
            }:
                # The UI header only identifies this feature; authorization
                # always comes from the HttpOnly Web session and provider link.
                if not self._identity_write_allowed():
                    return
                if self.headers.get("X-Leshy-UI") != "superagent":
                    return self._identity_json(HTTPStatus.FORBIDDEN, {
                        "error": "Требуется интерфейс SuperAgent",
                    })
                if (not module_enabled(app.storage, "superagent")
                        or not module_enabled(app.storage, "remote-admin")):
                    return self._identity_json(HTTPStatus.FORBIDDEN, {
                        "error": "Удалённое управление отключено",
                    })
                context = self._remote_context()
                if context is None:
                    return
                policy, actor = context
                try:
                    body = self._body()
                    if not isinstance(body, dict):
                        raise ValueError("Ожидается JSON-объект")
                    if route.endswith("/open"):
                        if (set(body) != {"node_id", "credential_file", "confirm"}
                                or body.get("confirm") is not True):
                            raise ValueError("Подтвердите открытие SSH-сеанса")
                        result = pty_broker.open(
                            policy, actor, body["node_id"], body["credential_file"],
                        )
                        return self._identity_json(HTTPStatus.OK, {"pty": result})
                    if route.endswith("/read"):
                        if set(body) != {"session_id"}:
                            raise ValueError("Укажите SSH-сеанс")
                        result = pty_broker.read(policy, actor, body["session_id"])
                        return self._identity_json(HTTPStatus.OK, {"pty": result})
                    if route.endswith("/prepare"):
                        if set(body) != {"session_id", "line"}:
                            raise ValueError("Нужна точная строка терминала")
                        result = pty_broker.prepare(
                            policy, actor, body["session_id"], body["line"],
                        )
                        return self._identity_json(HTTPStatus.OK, {"approval": result})
                    if route.endswith("/write"):
                        if (set(body) != {
                            "session_id", "line", "approval_token", "confirm",
                        } or body.get("confirm") is not True):
                            raise ValueError("Подтвердите строку SSH-терминала")
                        pty_broker.write(
                            policy, actor, body["session_id"],
                            body["line"], body["approval_token"],
                        )
                        return self._identity_json(HTTPStatus.OK, {
                            "pty": {"accepted": True, "outcome": "unverified"},
                        })
                    if set(body) != {"session_id"}:
                        raise ValueError("Укажите SSH-сеанс")
                    pty_broker.close(policy, actor, body["session_id"])
                    return self._identity_json(HTTPStatus.OK, {"pty": {"closed": True}})
                except ValueError as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {
                        "error": str(exc)[:250],
                    })
                except PermissionError as exc:
                    return self._identity_json(HTTPStatus.FORBIDDEN, {
                        "error": str(exc)[:250],
                    })
                except TimeoutError:
                    return self._identity_json(HTTPStatus.GATEWAY_TIMEOUT, {
                        "error": "SSH-сеанс превысил срок ожидания",
                    })
                except Exception as exc:
                    problem("superagent_ssh_pty_failed", type(exc).__name__)
                    return self._identity_json(HTTPStatus.BAD_GATEWAY, {
                        "error": "SSH-соединение прервано или состояние удалённой команды неизвестно",
                    })
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
            if route == "/v1/telegram/config":
                # This global administrator setting must never be writable
                # by an arbitrary Web guest, even when LESHY_API_TOKEN is unset.
                if not self._identity_write_allowed():
                    return
                current = telegram._access()
                current_owner = str(current.get("owner_id") or "").strip()
                creator = creator_telegram_id(telegram)
                role = role_for_web(telegram, identity, self._existing_web_owner())
                local = str(self.client_address[0]) in {"127.0.0.1", "::1"}
                if current_owner or creator:
                    if role not in {"creator", "server_owner"}:
                        return self._identity_json(HTTPStatus.FORBIDDEN, {
                            "error": "Настройки Telegram доступны только подтверждённому хозяину сервера",
                        })
                elif not local:
                    return self._identity_json(HTTPStatus.FORBIDDEN, {
                        "error": "Первичная настройка Telegram доступна только на localhost",
                    })
                try:
                    body = self._body()
                    # Never accept a caller-selected owner ID. New assignments
                    # require the separately verified local creator claim.
                    if "owner_id" in body and str(body["owner_id"] or "").strip() != current_owner:
                        raise PermissionError("Хозяина нельзя менять через настройки Telegram")
                    users = body.get("allowed_users", [])
                    if isinstance(users, str):
                        users = [part.strip() for part in users.replace(",", "\n").splitlines() if part.strip()]
                    if not isinstance(users, list):
                        raise ValueError("Неверный список разрешённых Telegram ID")
                    preserved_owner = current_owner or (creator if role == "creator" else "")
                    telegram.configure(
                        str(body.get("token") or ""), users,
                        allow_all=bool(body.get("allow_all", current.get("allow_all", False))),
                        owner_id=preserved_owner,
                        infer_owner_from_allowed=False,
                    )
                    telegram.start()
                    return self._identity_json(HTTPStatus.OK, telegram.status())
                except (ValueError, PermissionError) as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            if route == "/v1/identity/creator/claim":
                # A Telegram login and access to localhost do not establish
                # federation-wide creator authority. Never self-appoint here.
                return self._identity_json(HTTPStatus.FORBIDDEN, {
                    "error": "Самоназначение Создателя отключено. Вход через Telegram подтверждает только личность.",
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
