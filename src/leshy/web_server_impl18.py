"""Администрирование HTTP/HTTPS по подтверждённой сессии хозяина сервера."""
from __future__ import annotations

import hmac
from http import HTTPStatus
import ipaddress
import json
import os
import re
import ssl
from typing import Callable
from urllib.parse import urlsplit

from . import web_server_impl17 as previous
from . import web_server_impl3 as core
from .web_server_impl6 import prepare_runtime
from .diagnostics import problem
from .web_transport_runtime import listener_ready


_PREVIOUS_MAKE_HANDLER = previous.make_handler
_PREFIX = "/v1/web-security/"
_VOICE_PATHS = {"/v1/voice/transcribe", "/v1/voice/speak"}
_MAX_JSON = 512 * 1024


def _origin_tuple(value: str):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, TypeError):
        return None


def _unambiguous_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющиеся ключи JSON запрещены")
        result[key] = value
    return result


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    service = app.web_security

    class Handler(Parent):
        def _cookie_value(self, name: str) -> str:
            if self._real_tls() and name == app.owner_identity.cookie_name:
                name = "__Host-" + name
            return super()._cookie_value(name)

        def _identity_cookie_header(self, new_cookie: str | None) -> str:
            if not new_cookie:
                return ""
            secure = self._real_tls()
            name = ("__Host-" if secure else "") + app.owner_identity.cookie_name
            value = f"{name}={new_cookie}; Path=/; Max-Age={180 * 24 * 60 * 60}; HttpOnly; SameSite=Strict"
            return value + ("; Secure" if secure else "")

        def _real_tls(self) -> bool:
            return isinstance(self.connection, ssl.SSLSocket)

        def _loopback(self) -> bool:
            try:
                return ipaddress.ip_address(self.client_address[0]).is_loopback
            except (ValueError, TypeError, IndexError):
                return False

        def _private_import_allowed(self) -> bool:
            if self._real_tls():
                return True
            if not self._loopback():
                return False
            try:
                host = urlsplit("http://" + str(self.headers.get("Host") or "")).hostname
                return host == "localhost" or bool(host and ipaddress.ip_address(host).is_loopback)
            except ValueError:
                return False

        def _exact_origin(self, header: str) -> bool:
            scheme = "https" if self._real_tls() else "http"
            expected = _origin_tuple(scheme + "://" + str(self.headers.get("Host") or ""))
            actual = _origin_tuple(str(self.headers.get("Origin") or ""))
            return expected is not None and expected == actual and self.headers.get("X-Leshy-UI") == header

        def _web_security_owner(self, write: bool = False) -> bool:
            # Имя, bearer token и глобальная роль создателя не дают чужой сервер.
            owner = self._existing_web_owner()
            if not owner or owner == "web:local":
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Войдите в подтверждённый Web-профиль хозяина сервера"})
                return False
            try:
                configured = str(telegram._access().get("owner_id") or "").strip()
                links = app.owner_identity.status(owner).get("telegram", [])
                actual_owner = bool(re.fullmatch(r"[1-9][0-9]{1,19}", configured)) and any(
                    str(row.get("user_id") or "").strip() == configured for row in links)
            except Exception:
                actual_owner = False
            if not actual_owner:
                self._json(HTTPStatus.FORBIDDEN, {"error": "Настройки сети доступны только подтверждённому хозяину этого сервера"})
                return False
            if self.headers.get("X-Leshy-UI") != "network-security":
                self._json(HTTPStatus.FORBIDDEN, {"error": "Откройте настройки сети в интерфейсе Лешего"})
                return False
            if write and not self._exact_origin("network-security"):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Origin должен совпадать со схемой, именем и портом текущего интерфейса"})
                return False
            return True

        def _security_body(self) -> dict:
            if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError("Требуется Content-Type application/json")
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise ValueError("Требуется однозначный Content-Length без потокового кодирования")
            try:
                length = int(self.headers["Content-Length"])
            except (ValueError, TypeError) as exc:
                raise ValueError("Некорректная длина запроса") from exc
            if not 0 < length <= _MAX_JSON:
                raise ValueError("Размер запроса должен быть от 1 байта до 512 КБ")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Запрос не получен полностью")
            try:
                body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unambiguous_object)
            except (UnicodeError, ValueError) as exc:
                raise ValueError("Некорректный JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("Ожидается JSON-объект")
            return body

        def _voice_allowed(self) -> bool:
            # Машинный клиент допустим только с реально настроенным bearer token.
            token = os.getenv("LESHY_API_TOKEN", "").strip()
            authorization = str(self.headers.get("Authorization") or "")
            if token and not self._private_import_allowed():
                self._json(HTTPStatus.FORBIDDEN, {
                    "error": "Голосовой bearer token принимается только через HTTPS или локально на сервере"})
                return False
            if token and hmac.compare_digest(authorization.encode("utf-8"), ("Bearer " + token).encode("utf-8")):
                return True
            if token:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Для голосового API требуется настроенный bearer token"})
                return False
            if not self._existing_web_owner() or not self._exact_origin("voice"):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Для голоса нужны Web-сессия и запрос из текущего интерфейса"})
                return False
            return True

        def do_GET(self):
            route = urlsplit(self.path).path
            challenge_prefix = "/.well-known/acme-challenge/"
            if route.startswith(challenge_prefix):
                token = route[len(challenge_prefix):]
                value = service.acme.challenge(token) if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token) else None
                if value is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Активная проверка ACME не найдена"})
                encoded = value.encode("ascii")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=ascii")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)
                return
            if route == "/network-security-panel.js":
                try:
                    return self._send_asset("network-security-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Панель HTTP/HTTPS отсутствует в установке"})
            if route == _PREFIX + "local-ca/root.cer":
                if not self._web_security_owner():
                    return
                if not self._private_import_allowed():
                    return self._json(HTTPStatus.FORBIDDEN, {
                        "error": "Скачайте корневой сертификат через HTTPS или локально на самом сервере"})
                try:
                    encoded = service.local_ca.public_certificate()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/x-x509-ca-cert")
                    self.send_header("Content-Disposition", 'attachment; filename="Leshy-local-root.cer"')
                    self.send_header("Content-Length", str(len(encoded)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                except (ValueError, RuntimeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("web_local_ca_download_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Корневой сертификат недоступен"})
            if route == _PREFIX + "status":
                if not self._web_security_owner():
                    return
                try:
                    value = service.status()
                    value["connection"] = {"tls": self._real_tls(), "loopback": self._loopback(),
                                           "can_import_certificate": self._private_import_allowed(),
                                           "can_issue_local_ca": self._private_import_allowed(),
                                           "can_download_local_ca": self._private_import_allowed(),
                                           "separate_protocol_sessions": True}
                    return self._json(HTTPStatus.OK, value)
                except Exception as exc:
                    problem("web_security_status_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось прочитать состояние HTTP/HTTPS"})
            return super().do_GET()

        def do_POST(self):
            route = urlsplit(self.path).path
            if route in _VOICE_PATHS:
                if not self._voice_allowed():
                    return
                return super().do_POST()
            if not route.startswith(_PREFIX):
                return super().do_POST()
            if not self._web_security_owner(write=True):
                return
            try:
                body = self._security_body()
                if route == _PREFIX + "settings":
                    service.save_settings(body)
                elif route == _PREFIX + "certificate/import":
                    if not self._private_import_allowed():
                        return self._json(HTTPStatus.FORBIDDEN, {"error": "Передавать закрытый ключ можно только через HTTPS или локально на сервере"})
                    if body.get("confirm") is not True:
                        raise ValueError("Подтвердите импорт сертификата")
                    service.import_certificate(body.get("certificate_pem"), body.get("private_key_pem"),
                                               body.get("key_password"))
                elif route == _PREFIX + "acme/configure":
                    service.configure_acme(body)
                elif route == _PREFIX + "acme/issue":
                    service.issue_acme(body)
                elif route == _PREFIX + "local-ca/issue":
                    if not self._private_import_allowed():
                        return self._json(HTTPStatus.FORBIDDEN, {
                            "error": "Создайте локальный CA через HTTPS или локально на самом сервере"})
                    service.issue_local_certificate(body)
                elif route == _PREFIX + "local-ca/disable":
                    service.disable_local_ca(body)
                else:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Неизвестная операция HTTP/HTTPS"})
                return self._json(HTTPStatus.OK, service.status())
            except (ValueError, RuntimeError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                problem("web_security_write_failed", type(exc).__name__)
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Операция HTTP/HTTPS не выполнена; проверьте состояние настроек"})

    return Handler


def serve(host: str | None = None, port: int | None = None,
          on_listening: Callable[[str], None] | None = None) -> None:
    # Подготовка до LeshyApp сохранена; фабрика принадлежит конкретному запуску.
    # Старые serve-обёртки временно меняют глобальные фабрики и здесь не вызываются.
    prepare_runtime()
    with listener_ready(on_listening):
        core.serve(host=host, port=port, handler_factory=make_handler)


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
