"""Owner-scoped SuperAgent dashboard on the existing Web server.

No owner_id is accepted from query/body. Only an existing HttpOnly web
identity cookie may authorize task actions; every service call rechecks owner.
"""
from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl13 as previous
from .diagnostics import problem
from .module_settings import module_enabled
from .web_server_impl11 import _runtime_html
from .superagent_dashboard import execute_action, list_tickets, ticket_details
from .superagent_credential_settings import (
    get_credential_folder, set_credential_folder, list_credential_file_names,
)
from .superagent_verified_ssh import inspect_remote_os
from .superagent_remote_access_dashboard import (
    authorized_remote_context, list_remote_access, revoke_remote_grant,
)

_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)

    class Handler(Parent):
        def _settings_owner(self):
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется авторизация API"})
                return None
            owner_getter = getattr(self, "_existing_web_owner", None)
            owner = owner_getter() if callable(owner_getter) else ""
            if not owner or owner == "web:local":
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Откройте сайт с действующей Web-сессией"})
                return None
            return owner

        def _dashboard_context(self):
            owner = self._settings_owner()
            if owner is None:
                return None
            if not module_enabled(app.storage, "superagent"):
                self._json(HTTPStatus.CONFLICT, {"error": "Включите SuperAgent в настройках модулей"})
                return None
            service = getattr(app, "superagent", None)
            if service is None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "SuperAgent ещё не запущен"})
                return None
            return owner, service

        def _remote_context(self):
            # This inventory is strictly for existing, provider-linked Web
            # owners. An anonymous cookie / API key is not remote authority.
            owner = self._settings_owner()
            if owner is None:
                return None
            if not module_enabled(app.storage, "superagent"):
                self._json(HTTPStatus.CONFLICT, {
                    "error": "Включите SuperAgent в настройках модулей"
                })
                return None
            try:
                identity = getattr(app, "owner_identity", None)
                if identity is None:
                    raise PermissionError("Вход через провайдера не настроен")
                return authorized_remote_context(
                    app, owner, self._cookie_value(identity.cookie_name)
                )
            except (PermissionError, ValueError):
                self._json(HTTPStatus.FORBIDDEN, {
                    "error": "Для списка компьютеров требуется подтверждённый вход владельца"
                })
                return None

        def do_GET(self):
            parsed = parse.urlsplit(self.path)
            if parsed.path == "/":
                # Preserve the established owner identity bootstrap/cookie
                # while injecting one optional script into the Tasks panel.
                try:
                    owner, new_cookie, _session_hash = self._identity_context("web:local")
                    data = _runtime_html(settings, owner).decode("utf-8")
                except Exception as exc:
                    problem("superagent_dashboard_page_bootstrap_failed", type(exc).__name__)
                    new_cookie = None
                    data = _runtime_html(settings).decode("utf-8")
                for marker in (
                    '<script src="/admin-dashboard.js"></script>',
                    '<script src="/superagent-dashboard.js"></script>',
                    '<script src="/superagent-credential-folder.js"></script>',
                ):
                    if marker not in data:
                        data = data.replace("</body>", marker + "\n</body>")
                encoded = data.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                cookie = self._identity_cookie_header(new_cookie)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(encoded)
                return
            if parsed.path == "/admin-dashboard.js":
                try:
                    return self._send_asset("admin-dashboard.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Панель управления не найдена"})
            if parsed.path == "/superagent-dashboard.js":
                try:
                    return self._send_asset("superagent-dashboard.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Панель SuperAgent не найдена"})
            if parsed.path == "/superagent-credential-folder.js":
                try:
                    return self._send_asset("superagent-credential-folder.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Настройки SuperAgent не найдены"})
            if parsed.path == "/v1/superagent/credential-folder":
                owner = self._settings_owner()
                if owner is None:
                    return None
                try:
                    return self._json(HTTPStatus.OK, get_credential_folder(app.storage, owner))
                except Exception as exc:
                    problem("superagent_credential_folder_get_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось прочитать настройку папки"})
            if parsed.path == "/v1/superagent/ssh/credentials":
                context = self._remote_context()
                if context is None:
                    return None
                if not module_enabled(app.storage, "remote-admin"):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Удалённая диагностика выключена"
                    })
                policy, actor = context
                try:
                    return self._json(HTTPStatus.OK, list_credential_file_names(
                        policy.storage, actor.owner_id,
                    ))
                except (ValueError, PermissionError, OSError) as exc:
                    problem("superagent_ssh_credential_picker_denied", type(exc).__name__)
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Не удалось выбрать файлы доступа; проверьте настройки папки"
                    })
            if parsed.path == "/v1/superagent/remote-access":
                context = self._remote_context()
                if context is None:
                    return None
                policy, actor = context
                try:
                    return self._json(HTTPStatus.OK, list_remote_access(
                        policy, actor,
                        remote_admin_enabled=module_enabled(app.storage, "remote-admin"),
                    ))
                except Exception as exc:
                    problem("superagent_remote_access_list_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось получить список удалённых компьютеров"
                    })
            if parsed.path != "/v1/superagent/tasks":
                return super().do_GET()
            context = self._dashboard_context()
            if context is None:
                return None
            owner, service = context
            try:
                query = parse.parse_qs(parsed.query)
                ids = query.get("id") or []
                if ids:
                    if len(ids) != 1 or len(ids[0]) > 100:
                        raise ValueError("Некорректный идентификатор")
                    return self._json(HTTPStatus.OK, ticket_details(service, owner, ids[0]))
                raw_archived = query.get("archived", ["0"])
                if len(raw_archived) != 1 or raw_archived[0] not in {"0", "1"}:
                    raise ValueError("Некорректный режим архива")
                return self._json(HTTPStatus.OK, list_tickets(service, owner, archived=raw_archived[0] == "1"))
            except (KeyError, ValueError, PermissionError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                problem("superagent_dashboard_list_failed", type(exc).__name__)
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось получить задания"})

        def do_POST(self):
            path = parse.urlsplit(self.path).path
            if path == "/v1/superagent/credential-folder":
                owner = self._settings_owner()
                if owner is None:
                    return None
                if self.headers.get("X-Leshy-UI") != "superagent":
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "Недопустимый источник запроса"})
                origin = str(self.headers.get("Origin") or "")
                if origin and parse.urlsplit(origin).netloc.lower() != str(self.headers.get("Host") or "").lower():
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "Чужой источник запроса"})
                if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "Требуется JSON"})
                try:
                    payload = self._body()
                    if not isinstance(payload, dict) or set(payload) != {"path"}:
                        raise ValueError("Требуется только поле path")
                    return self._json(HTTPStatus.OK, set_credential_folder(app.storage, owner, payload["path"]))
                except (ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("superagent_credential_folder_set_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось сохранить настройку"})
            if path == "/v1/superagent/ssh/inspect":
                context = self._remote_context()
                if context is None:
                    return None
                if not module_enabled(app.storage, "remote-admin"):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Включите удалённую диагностику в настройках"
                    })
                if self.headers.get("X-Leshy-UI") != "superagent":
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Недопустимый источник запроса"
                    })
                origin = str(self.headers.get("Origin") or "")
                if origin and parse.urlsplit(origin).netloc.lower() != str(self.headers.get("Host") or "").lower():
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Чужой источник запроса"
                    })
                if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "Требуется JSON"})
                try:
                    payload = self._body()
                    if (not isinstance(payload, dict) or
                            set(payload) != {"node_id", "credential_file", "confirm"} or
                            payload.get("confirm") is not True or
                            not isinstance(payload["node_id"], str) or
                            not isinstance(payload["credential_file"], str)):
                        raise ValueError("Выберите компьютер, файл доступа и подтвердите проверку")
                    policy, actor = context
                    result = inspect_remote_os(
                        policy, actor, payload["node_id"], payload["credential_file"]
                    )
                    # Results intentionally omit host keys and any command output.
                    return self._json(HTTPStatus.OK, {"inspection": {
                        "node_id": result["node_id"],
                        "os_family": result["os_family"],
                        "verified": bool(result["verified"]),
                        "method": result["method"],
                    }})
                except (ValueError, PermissionError, FileNotFoundError) as exc:
                    problem("superagent_ssh_inspection_denied", type(exc).__name__)
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Компьютер не подтверждён, нет прав или неверно выбран файл доступа"
                    })
                except Exception as exc:
                    problem("superagent_ssh_inspection_failed", type(exc).__name__)
                    return self._json(HTTPStatus.BAD_GATEWAY, {
                        "error": "SSH-проверка не завершена: проверьте связь и установленный модуль SSH"
                    })
            if path == "/v1/superagent/remote-access/revoke":
                # Only revocation is exposed; no enroll/grant/OS mutation route.
                context = self._remote_context()
                if context is None:
                    return None
                if self.headers.get("X-Leshy-UI") != "superagent":
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Недопустимый источник запроса"
                    })
                origin = str(self.headers.get("Origin") or "")
                if origin and parse.urlsplit(origin).netloc.lower() != str(self.headers.get("Host") or "").lower():
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Чужой источник запроса"
                    })
                if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "Требуется JSON"})
                policy, actor = context
                try:
                    return self._json(HTTPStatus.OK, revoke_remote_grant(
                        policy, actor, self._body()
                    ))
                except (PermissionError, ValueError):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Нельзя отозвать это разрешение"
                    })
                except Exception as exc:
                    problem("superagent_remote_access_revoke_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось отозвать разрешение"
                    })
            if path != "/v1/superagent/task":
                return super().do_POST()
            context = self._dashboard_context()
            if context is None:
                return None
            # Explicit same-origin custom header prevents cross-site form POSTs;
            # the session cookie is SameSite=Strict and HttpOnly as well.
            if self.headers.get("X-Leshy-UI") != "superagent":
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Недопустимый источник запроса"})
            origin = str(self.headers.get("Origin") or "")
            if origin and parse.urlsplit(origin).netloc.lower() != str(self.headers.get("Host") or "").lower():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Чужой источник запроса"})
            if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Требуется JSON"})
            owner, service = context
            try:
                return self._json(HTTPStatus.OK, execute_action(service, owner, self._body()))
            except (KeyError, ValueError, PermissionError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                problem("superagent_dashboard_action_failed", type(exc).__name__)
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Команда не выполнена"})

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
