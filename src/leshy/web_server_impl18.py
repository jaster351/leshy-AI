"""Owner-confirmed Android enrollment, voice-only per-device access."""
from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from . import web_server_impl17 as previous
from .android_device_pairing import AndroidPairing
from .android_voice_access import (
    authorized_owner as legacy_voice_owner,
    conversational_answer,
)
from .diagnostics import problem
from .role_authority import role_for_web


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    identity = app.owner_identity
    pairing = getattr(app, "_android_pairing", None)
    if not isinstance(pairing, AndroidPairing):
        pairing = AndroidPairing(settings.brain_dir)
        app._android_pairing = pairing

    class Handler(Parent):
        def _android_verified_owner(self, *, writing: bool = False) -> str | None:
            # Anonymous/guest Web cookies, spoofed owner_id and the Android's
            # self-reported public key never confer privilege to approve access.
            owner = self._existing_web_owner()
            if not owner or role_for_web(telegram, identity, owner) not in {
                "creator", "server_owner"
            }:
                self._identity_json(HTTPStatus.FORBIDDEN, {
                    "error": "Подтвердите вход в Web как хозяин сервера через Telegram"
                })
                return None
            if writing:
                if "application/json" not in str(self.headers.get("Content-Type", "")).lower():
                    self._identity_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {
                        "error": "Ожидается application/json"
                    })
                    return None
                # Same-origin JSON + verified HttpOnly Web cookie. Never use a
                # query/string owner_id, a localStorage role or a voice token.
                origin = str(self.headers.get("Origin") or "")
                referer = str(self.headers.get("Referer") or "")
                source = origin or referer
                host = str(self.headers.get("Host") or "").lower().strip()
                try:
                    url = urlsplit(source)
                    same_origin = (url.scheme in {"http", "https"} and
                                   bool(host) and url.netloc.lower() == host)
                except ValueError:
                    same_origin = False
                if not same_origin:
                    self._identity_json(HTTPStatus.FORBIDDEN, {
                        "error": "Подтверждение должно поступить из этого Web-интерфейса"
                    })
                    return None
            return owner

        def _android_voice_actor(self):
            authorization = self.headers.get("Authorization", "")
            return pairing.authorize(authorization) or legacy_voice_owner(
                settings.brain_dir, authorization
            )

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/android/pair":
                return self._send_asset("android-pair.html", "text/html; charset=utf-8")
            if route == "/v1/android/voice/status":
                if not self._android_voice_actor():
                    return self._json(HTTPStatus.UNAUTHORIZED, {
                        "error": "Android conversation access not granted"
                    })
                return self._json(HTTPStatus.OK, {
                    "ready": True, "mode": "conversation_only"
                })
            if route in {"/v1/android/pair/request", "/v1/android/pair/devices"}:
                owner = self._android_verified_owner()
                if not owner:
                    return
                try:
                    if route.endswith("/devices"):
                        return self._identity_json(HTTPStatus.OK, {
                            "items": pairing.list_devices(owner)
                        })
                    query = parse_qs(urlsplit(self.path).query)
                    request_id = str((query.get("id") or [""])[0])
                    return self._identity_json(HTTPStatus.OK, pairing.owner_request(request_id))
                except (ValueError, PermissionError) as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {
                        "error": str(exc)[:200]
                    })
            return super().do_GET()

        def do_POST(self):
            route = urlsplit(self.path).path
            if route == "/v1/android/voice/chat":
                actor = self._android_voice_actor()
                if not actor:
                    return self._json(HTTPStatus.UNAUTHORIZED, {
                        "error": "Android conversation access not granted"
                    })
                try:
                    return self._json(HTTPStatus.OK, conversational_answer(
                        app, actor, self._body()
                    ))
                except (ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:200]})
                except Exception as exc:
                    problem("android_paired_voice_chat_failed", type(exc).__name__)
                    return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {
                        "error": "Android conversation temporarily unavailable"
                    })
            anonymous = {
                "/v1/android/pair/start",
                "/v1/android/pair/prove",
                "/v1/android/pair/status",
            }
            privileged = {
                "/v1/android/pair/approve",
                "/v1/android/pair/revoke",
            }
            if route not in anonymous | privileged:
                return super().do_POST()
            owner = None
            if route in privileged:
                owner = self._android_verified_owner(writing=True)
                if not owner:
                    return
            elif "application/json" not in str(self.headers.get("Content-Type", "")).lower():
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {
                    "error": "Expected JSON"
                })
            try:
                # Unauthenticated pairing endpoints must not inherit the
                # generic handler's 2 MiB request-body allowance.
                length = int(self.headers.get("Content-Length", "0") or "0")
                if not 1 <= length <= 4096:
                    return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
                        "error": "Pairing request must be 1 to 4096 bytes"
                    })
                body = self._body()
                if route.endswith("/start"):
                    result = pairing.begin(body, str(self.client_address[0]))
                    return self._json(HTTPStatus.CREATED, result)
                if route.endswith("/prove"):
                    return self._json(HTTPStatus.OK, pairing.prove(body))
                if route.endswith("/status"):
                    return self._json(HTTPStatus.OK, pairing.poll(body))
                if route.endswith("/approve"):
                    if set(body) != {"request_id", "confirm"} or body["confirm"] is not True:
                        raise PermissionError("Explicit owner confirmation required")
                    return self._identity_json(
                        HTTPStatus.OK, pairing.approve(body["request_id"], owner)
                    )
                if set(body) != {"device_id"}:
                    raise ValueError("Expected device_id")
                removed = pairing.revoke(owner, body["device_id"])
                return self._identity_json(
                    HTTPStatus.OK if removed else HTTPStatus.NOT_FOUND,
                    {"revoked": removed}
                )
            except (ValueError, TypeError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:200]})
            except PermissionError as exc:
                return self._json(HTTPStatus.FORBIDDEN, {"error": str(exc)[:200]})
            except Exception as exc:
                problem("android_device_pairing_failed", type(exc).__name__, route=route)
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": "Pairing request failed"
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
