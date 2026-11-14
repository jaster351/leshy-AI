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
from .superagent_ssh_enrollment import enroll_verified_ssh_node
from .superagent_rdp_profile import rdp_connection_profile
from .superagent_remote_gateway import get_remote_gateway, set_remote_gateway
from .superagent_ssh_terminal import run_ssh_terminal_command
from .superagent_ssh_approvals import (
    prepare_ssh_command, consume_ssh_approval,
    prepare_ssh_checked_step, consume_ssh_checked_approval,
    prepare_ssh_plan, consume_ssh_plan_approval,
)
from .superagent_ssh_jobs import (
    start_ssh_plan_job, list_ssh_plan_jobs,
    cancel_ssh_plan_job, acknowledge_ssh_plan_job,
)
from .superagent_ssh_checked_step import run_ssh_checked_step
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
            if parsed.path == "/v1/superagent/remote-gateway":
                context = self._remote_context()
                if context is None:
                    return None
                policy, actor = context
                try:
                    return self._json(HTTPStatus.OK, {
                        "gateway": get_remote_gateway(policy, actor)
                    })
                except Exception as exc:
                    problem("superagent_remote_gateway_get_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось получить настройки шлюза"
                    })
            if parsed.path == "/v1/superagent/rdp/profile":
                context = self._remote_context()
                if context is None:
                    return None
                if not module_enabled(app.storage, "remote-admin"):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Удалённое управление выключено"
                    })
                try:
                    parameters = parse.parse_qs(parsed.query)
                    if set(parameters) != {"node_id"} or len(parameters["node_id"]) != 1:
                        raise ValueError("Выберите зарегистрированный компьютер")
                    policy, actor = context
                    return self._json(HTTPStatus.OK, {
                        "profile": rdp_connection_profile(
                            policy, actor, parameters["node_id"][0],
                        )
                    })
                except ValueError:
                    return self._json(HTTPStatus.BAD_REQUEST, {
                        "error": "Некорректный RDP-адрес"
                    })
                except PermissionError:
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "У Вас нет доступа к этому RDP-подключению"
                    })
                except Exception as exc:
                    problem("superagent_rdp_profile_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось подготовить RDP-подключение"
                    })
            if parsed.path == "/v1/superagent/ssh/plan/jobs":
                context = self._remote_context()
                if context is None:
                    return None
                policy, actor = context
                try:
                    query = parse.parse_qs(parsed.query)
                    if set(query) - {"id"} or len(query.get("id", [])) > 1:
                        raise ValueError("Некорректный запрос состояния SSH-плана")
                    job_id = query.get("id", [""])[0]
                    return self._json(HTTPStatus.OK, list_ssh_plan_jobs(
                        policy, actor, job_id=job_id,
                    ))
                except (ValueError, PermissionError):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "SSH-план не найден или нет разрешения"
                    })
                except Exception as exc:
                    problem("superagent_ssh_plan_status_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось прочитать состояние SSH-плана"
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
            if path == "/v1/superagent/remote-gateway":
                context = self._remote_context()
                if context is None:
                    return None
                if not module_enabled(app.storage, "remote-admin"):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Удалённое управление выключено"
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
                policy, actor = context
                try:
                    payload = self._body()
                    if (not isinstance(payload, dict) or set(payload) != {"url", "confirm"}
                            or payload.get("confirm") is not True):
                        raise ValueError("Подтвердите адрес SSH/RDP-шлюза")
                    return self._json(HTTPStatus.OK, {
                        "gateway": set_remote_gateway(policy, actor, payload["url"])
                    })
                except ValueError:
                    return self._json(HTTPStatus.BAD_REQUEST, {
                        "error": "Неверный адрес шлюза: используйте HTTPS либо локальный 127.0.0.1"
                    })
                except PermissionError:
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Владелец не подтверждён"
                    })
                except Exception as exc:
                    problem("superagent_remote_gateway_save_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось сохранить адрес шлюза"
                    })
            if path in {
                "/v1/superagent/ssh/plan/jobs/cancel",
                "/v1/superagent/ssh/plan/jobs/review",
            }:
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
                    payload = self._body()
                    if not isinstance(payload, dict) or set(payload) != {"job_id", "confirm"} or payload.get("confirm") is not True:
                        raise ValueError("Требуется явное подтверждение действия")
                    if path.endswith("/cancel"):
                        job = cancel_ssh_plan_job(policy, actor, payload["job_id"])
                    else:
                        job = acknowledge_ssh_plan_job(policy, actor, payload["job_id"])
                    return self._json(HTTPStatus.OK, {"ssh_job": job})
                except (PermissionError, ValueError):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Нет прав, задание активно или неверное подтверждение"
                    })
                except Exception as exc:
                    problem("superagent_ssh_plan_control_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": "Не удалось изменить состояние SSH-плана"
                    })
            if path in {
                "/v1/superagent/ssh/enroll",
                "/v1/superagent/ssh/prepare",
                "/v1/superagent/ssh/execute",
                "/v1/superagent/ssh/check/prepare",
                "/v1/superagent/ssh/check/execute",
                "/v1/superagent/ssh/plan/prepare",
                "/v1/superagent/ssh/plan/execute",
            }:
                context = self._remote_context()
                if context is None:
                    return None
                if not module_enabled(app.storage, "remote-admin"):
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Удалённое управление выключено"
                    })
                if self.headers.get("X-Leshy-UI") != "superagent":
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Недопустимый источник SSH-запроса"
                    })
                origin = str(self.headers.get("Origin") or "")
                if origin and parse.urlsplit(origin).netloc.lower() != str(self.headers.get("Host") or "").lower():
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Чужой источник SSH-запроса"
                    })
                if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                    return self._json(HTTPStatus.BAD_REQUEST, {
                        "error": "Требуется JSON"
                    })
                policy, actor = context
                try:
                    payload = self._body()
                    if not isinstance(payload, dict):
                        raise ValueError("Нужен объект запроса")
                    if path == "/v1/superagent/ssh/enroll":
                        if set(payload) != {
                            "node_id", "credential_file", "expected_fingerprint", "confirm"
                        } or payload.get("confirm") is not True:
                            raise ValueError("Подтвердите регистрацию компьютера")
                        return self._json(HTTPStatus.OK, {
                            "enrollment": enroll_verified_ssh_node(
                                policy, actor, payload["node_id"],
                                payload["credential_file"], payload["expected_fingerprint"],
                            ),
                        })
                    if path == "/v1/superagent/ssh/prepare":
                        if set(payload) != {"node_id", "credential_file", "command"}:
                            raise ValueError("Выберите компьютер и команду")
                        return self._json(HTTPStatus.OK, {
                            "approval": prepare_ssh_command(
                                policy, actor, payload["node_id"],
                                payload["credential_file"], payload["command"],
                            ),
                        })
                    if path == "/v1/superagent/ssh/plan/prepare":
                        if set(payload) != {"node_id", "credential_file", "steps"}:
                            raise ValueError("Выберите компьютер и шаги плана")
                        return self._json(HTTPStatus.OK, {
                            "approval": prepare_ssh_plan(
                                policy, actor, payload["node_id"],
                                payload["credential_file"], payload["steps"],
                            )
                        })
                    if path == "/v1/superagent/ssh/plan/execute":
                        if (set(payload) != {
                            "node_id", "credential_file", "steps",
                            "approval_token", "confirm",
                        } or payload.get("confirm") is not True):
                            raise ValueError("Подтвердите точную последовательность SSH-команд")
                        consume_ssh_plan_approval(
                            policy, actor, payload["approval_token"], payload["node_id"],
                            payload["credential_file"], payload["steps"],
                        )
                        job = start_ssh_plan_job(
                            policy, actor, payload["node_id"],
                            payload["credential_file"], payload["steps"],
                            may_continue=lambda: module_enabled(app.storage, "remote-admin"),
                        )
                        return self._json(HTTPStatus.ACCEPTED, {"ssh_job": job})
                    if path == "/v1/superagent/ssh/check/prepare":
                        if set(payload) != {
                            "node_id", "credential_file", "command",
                            "verification_command", "expected_text",
                        }:
                            raise ValueError("Нужна команда и её проверка")
                        return self._json(HTTPStatus.OK, {
                            "approval": prepare_ssh_checked_step(
                                policy, actor, payload["node_id"],
                                payload["credential_file"], payload["command"],
                                payload["verification_command"], payload["expected_text"],
                            )
                        })
                    if path == "/v1/superagent/ssh/check/execute":
                        if (set(payload) != {
                            "node_id", "credential_file", "command",
                            "verification_command", "expected_text",
                            "approval_token", "confirm",
                        } or payload.get("confirm") is not True):
                            raise ValueError("Подтвердите обе точные SSH-команды")
                        consume_ssh_checked_approval(
                            policy, actor, payload["approval_token"], payload["node_id"],
                            payload["credential_file"], payload["command"],
                            payload["verification_command"], payload["expected_text"],
                        )
                        checked = run_ssh_checked_step(
                            policy, actor, payload["node_id"],
                            payload["credential_file"], payload["command"],
                            payload["verification_command"], payload["expected_text"],
                        )
                        return self._json(HTTPStatus.OK, {"checked_step": checked})
                    if set(payload) != {
                        "node_id", "credential_file", "command", "approval_token", "confirm"
                    } or payload.get("confirm") is not True:
                        raise ValueError("Подтвердите точную SSH-команду")
                    consume_ssh_approval(
                        policy, actor, payload["approval_token"], payload["node_id"],
                        payload["credential_file"], payload["command"],
                    )
                    result = run_ssh_terminal_command(
                        policy, actor, payload["node_id"],
                        payload["credential_file"], payload["command"],
                    )
                    return self._json(HTTPStatus.OK, {"execution": result})
                except ValueError:
                    return self._json(HTTPStatus.BAD_REQUEST, {
                        "error": "Неверные параметры SSH. Проверьте выбранные значения."
                    })
                except PermissionError:
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Отказ в SSH-доступе, подтверждении ключа или полномочиях"
                    })
                except TimeoutError:
                    return self._json(HTTPStatus.GATEWAY_TIMEOUT, {
                        "error": "SSH-сервер не ответил вовремя"
                    })
                except Exception as exc:
                    problem("superagent_ssh_operation_failed", type(exc).__name__)
                    if path in {
                        "/v1/superagent/ssh/check/execute",
                        "/v1/superagent/ssh/plan/execute",
                    }:
                        return self._json(HTTPStatus.BAD_GATEWAY, {
                            "error": "Результат операции неизвестен. Команда могла выполниться; "
                                     "проверьте состояние сервера до повторной попытки."
                        })
                    return self._json(HTTPStatus.BAD_GATEWAY, {
                        "error": "SSH-операция не подтверждена; команда могла выполниться"
                    })
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
                        "distribution": str(result.get("distribution") or "")[:82],
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
