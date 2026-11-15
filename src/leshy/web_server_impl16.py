"""Opt-in Gravatar and browser pairing while Leshy is available by private IP.

Google/Apple/VK are reported as unavailable until supported OAuth callbacks
are actually deployed. This module never accepts unverified provider identities.
"""
from __future__ import annotations

from http import HTTPStatus
from urllib.parse import urlsplit

from . import web_server_impl15 as previous
from .diagnostics import problem
from .profile_auth_providers import provider_status


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    identity = app.owner_identity

    class Handler(Parent):
        def _identity_status_payload(self, owner_id: str) -> dict:
            payload = super()._identity_status_payload(owner_id)
            payload["providers"] = provider_status()
            payload["browser_pairing_available"] = True
            payload["ip_without_domain_supported"] = True
            return payload

        def _identity_write_allowed(self) -> bool:
            # A real server-side browser session is mandatory. An arbitrary
            # owner_id from JSON, query strings or localStorage grants nothing.
            if not self._authorized() or not self._existing_web_owner():
                self._identity_json(HTTPStatus.UNAUTHORIZED, {
                    "error": "Требуется действующая Web-сессия Лешего",
                })
                return False
            if "application/json" not in str(self.headers.get("Content-Type", "")).lower():
                self._identity_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {
                    "error": "Требуется application/json",
                })
                return False
            origin = str(self.headers.get("Origin") or "")
            referer = str(self.headers.get("Referer") or "")
            source = origin or referer
            expected_host = str(self.headers.get("Host") or "").lower().strip()
            try:
                parts = urlsplit(source)
                valid = (
                    bool(source)
                    and parts.scheme in {"http", "https"}
                    and bool(expected_host)
                    and parts.netloc.lower() == expected_host
                )
            except ValueError:
                valid = False
            if not valid:
                self._identity_json(HTTPStatus.FORBIDDEN, {
                    "error": "Запрос должен поступать из текущего Web-интерфейса",
                })
                return False
            return True

        def do_POST(self):
            route = urlsplit(self.path).path
            if route not in {
                "/v1/identity/gravatar",
                "/v1/identity/gravatar/clear",
                "/v1/identity/browser/pair",
                "/v1/identity/browser/accept",
            }:
                return super().do_POST()
            if not self._identity_write_allowed():
                return
            owner = self._existing_web_owner()
            session_hash = identity._hash_secret(
                self._cookie_value(identity.cookie_name)
            )
            try:
                body = self._body()
                if not isinstance(body, dict):
                    raise ValueError("Требуется JSON-объект")
                if route == "/v1/identity/gravatar":
                    avatar = identity.configure_gravatar(
                        owner, str(body.get("email") or "")
                    )
                    return self._identity_json(HTTPStatus.OK, {"avatar": avatar})
                if route == "/v1/identity/gravatar/clear":
                    avatar = identity.clear_gravatar(owner)
                    return self._identity_json(HTTPStatus.OK, {"avatar": avatar})
                if route == "/v1/identity/browser/pair":
                    result = identity.create_browser_pairing(owner, session_hash)
                    return self._identity_json(HTTPStatus.OK, result)
                result = identity.consume_browser_pairing(
                    str(body.get("code") or ""), session_hash
                )
                return self._identity_json(HTTPStatus.OK, result)
            except (ValueError, PermissionError) as exc:
                return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                problem("owner_identity_profile_action_failed", type(exc).__name__,
                        route=route)
                return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": "Не удалось обновить профиль",
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
