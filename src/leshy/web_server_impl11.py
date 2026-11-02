from __future__ import annotations

from http import HTTPStatus
from http.cookies import SimpleCookie
import json
from urllib import parse

from . import web_server_impl10 as previous
from .app import LeshyApp
from .diagnostics import problem
from .owner_identity import OwnerIdentityService
from .owner_identity_runtime import install_owner_identity_runtime


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def _runtime_html(settings, owner_id: str = "") -> bytes:
    html_text = previous._runtime_html(settings).decode("utf-8")
    # Bootstrap ставится ДО старого inline runtime, чтобы даже самый первый
    # запрос страницы уже использовал UUID этого браузера, а не общий web:local.
    if owner_id:
        safe_owner = json.dumps(str(owner_id), ensure_ascii=False)
        bootstrap = f"<script>try{{localStorage.setItem('leshy.owner_id',{safe_owner});}}catch(_e){{}}</script>"
        first_script = html_text.find("<script>")
        if first_script >= 0:
            html_text = html_text[:first_script] + bootstrap + "\n" + html_text[first_script:]
        else:
            html_text = html_text.replace("</body>", bootstrap + "\n</body>")
    marker = '<script src="/owner-identity-panel.js"></script>'
    if marker not in html_text:
        html_text = html_text.replace("</body>", marker + "\n</body>")
    return html_text.encode("utf-8")


def make_handler(app, telegram, settings, model_catalog):
    identity = getattr(app, "owner_identity", None)
    if not isinstance(identity, OwnerIdentityService):
        identity = OwnerIdentityService(app.storage, settings.brain_dir)
        app.owner_identity = identity
    telegram.owner_identity = identity

    # Сначала предыдущие слои ставят свои chat wrappers/idempotency, затем
    # Owner Identity становится внешним слоем и нормализует owner_id.
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    install_owner_identity_runtime(LeshyApp, app, telegram)

    class Handler(Parent):
        def _cookie_value(self, name: str) -> str:
            raw = str(self.headers.get("Cookie") or "")
            if not raw:
                return ""
            try:
                cookie = SimpleCookie(); cookie.load(raw)
                morsel = cookie.get(name)
                return str(morsel.value) if morsel is not None else ""
            except Exception:
                return ""

        def _identity_context(self, presented_owner: str = "web:local"):
            return identity.ensure_web_session(
                self._cookie_value(identity.cookie_name),
                str(presented_owner or "web:local")[:200],
            )

        def _existing_web_owner(self) -> str:
            return identity.owner_for_web_cookie(self._cookie_value(identity.cookie_name))

        def _identity_cookie_header(self, new_cookie: str | None) -> str:
            if not new_cookie:
                return ""
            value = (
                f"{identity.cookie_name}={new_cookie}; Path=/; Max-Age={180 * 24 * 60 * 60}; "
                "HttpOnly; SameSite=Strict"
            )
            if str(self.headers.get("X-Forwarded-Proto") or "").lower() == "https":
                value += "; Secure"
            return value

        def _identity_json(self, status: int, payload: dict, new_cookie: str | None = None):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            cookie_header = self._identity_cookie_header(new_cookie)
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.end_headers()
            self.wfile.write(data)
            return None

        def _identity_status_payload(self, owner_id: str) -> dict:
            payload = identity.status(owner_id)
            try:
                tg = telegram.status()
            except Exception:
                tg = {}
            bot_username = str(tg.get("bot_username") or "").lstrip("@").strip()
            payload.update({
                "telegram_bot_username": bot_username,
                "telegram_chat_without_web_auth": True,
                "unlinked_telegram_isolated": True,
            })
            return payload

        def _rewrite_owner_query(self) -> None:
            owner_id = self._existing_web_owner()
            if not owner_id:
                return
            parsed = parse.urlparse(self.path)
            if not parsed.path.startswith("/v1/"):
                return
            pairs = parse.parse_qsl(parsed.query, keep_blank_values=True)
            changed = False
            rewritten = []
            for key, value in pairs:
                if key == "owner_id":
                    value = owner_id
                    changed = True
                rewritten.append((key, value))
            if changed:
                self.path = parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parse.urlencode(rewritten), parsed.fragment))

        def _body(self):
            body = super()._body()
            if isinstance(body, dict) and "owner_id" in body:
                owner_id = self._existing_web_owner()
                if owner_id:
                    body["owner_id"] = owner_id
            return body

        def do_GET(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/":
                try:
                    owner_id, new_cookie, _session_hash = self._identity_context("web:local")
                    data = _runtime_html(settings, owner_id)
                except Exception as exc:
                    problem("owner_identity_page_bootstrap_failed", str(exc))
                    owner_id, new_cookie = "", None
                    data = _runtime_html(settings)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                cookie_header = self._identity_cookie_header(new_cookie)
                if cookie_header:
                    self.send_header("Set-Cookie", cookie_header)
                self.end_headers()
                self.wfile.write(data)
                return

            if parsed.path == "/owner-identity-panel.js":
                try:
                    return self._send_asset("owner-identity-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "owner-identity-panel.js не найден"})

            if parsed.path == "/v1/identity/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                args = parse.parse_qs(parsed.query)
                presented = str((args.get("owner_id") or ["web:local"])[0])[:200]
                try:
                    owner_id, new_cookie, _session_hash = self._identity_context(presented)
                    return self._identity_json(HTTPStatus.OK, self._identity_status_payload(owner_id), new_cookie)
                except Exception as exc:
                    problem("owner_identity_status_failed", str(exc))
                    return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            # Старый UI всё ещё умеет присылать owner_id сам. Серверная cookie
            # является источником истины и подменяет query до любого parent handler.
            self._rewrite_owner_query()
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)

            if parsed.path == "/v1/identity/telegram/link":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner_id, new_cookie, web_session_hash = self._identity_context(str(body.get("owner_id") or "web:local"))
                    token = identity.create_telegram_link(owner_id, web_session_hash)
                    try:
                        tg = telegram.status()
                    except Exception:
                        tg = {}
                    bot_username = str(tg.get("bot_username") or "").lstrip("@").strip()
                    deep_link = f"https://t.me/{bot_username}?start=link_{token['code']}" if bot_username else ""
                    payload = {
                        **token,
                        "owner_id": owner_id,
                        "bot_username": bot_username,
                        "deep_link": deep_link,
                        "command": f"/link {token['code']}",
                        "telegram_already_linked": bool(identity.status(owner_id).get("telegram_linked")),
                    }
                    return self._identity_json(HTTPStatus.OK, payload, new_cookie)
                except (ValueError, PermissionError) as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("owner_identity_link_create_failed", str(exc))
                    return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/identity/telegram/unlink":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner_id, new_cookie, _session_hash = self._identity_context(str(body.get("owner_id") or "web:local"))
                    payload = identity.unlink_telegram(owner_id, str(body.get("telegram_user_id") or ""))
                    payload.update({"telegram_chat_without_web_auth": True, "unlinked_telegram_isolated": True})
                    return self._identity_json(HTTPStatus.OK, payload, new_cookie)
                except (ValueError, PermissionError) as exc:
                    return self._identity_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("owner_identity_unlink_failed", str(exc))
                    return self._identity_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            return super().do_POST()

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
