from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl7 as previous
from .app import LeshyApp
from .browser_platform_runtime import BrowserPlatform, install_browser_platform_routing
from .diagnostics import problem
from .extension_marketplace import MarketplaceEntry
from .tools_gateway import ToolManifest


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def _runtime_html(settings) -> bytes:
    html_text = previous._runtime_html(settings).decode("utf-8")
    marker = '<script src="/browser-approval-panel.js"></script>'
    if marker not in html_text:
        html_text = html_text.replace("</body>", marker + "\n</body>")
    return html_text.encode("utf-8")


def _platform(app) -> BrowserPlatform:
    value = getattr(app, "browser_platform", None)
    if not isinstance(value, BrowserPlatform):
        install_browser_platform_routing(LeshyApp, app)
        value = getattr(app, "browser_platform", None)
    if not isinstance(value, BrowserPlatform):
        raise RuntimeError("Browser Platform не инициализирован")
    return value


def make_handler(app, telegram, settings, model_catalog):
    # Ставим browser routing до impl7: общая idempotency-обёртка impl7 останется внешней.
    install_browser_platform_routing(LeshyApp, app)
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)

    class Handler(Parent):
        def do_GET(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/":
                data = _runtime_html(settings)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/browser-approval-panel.js":
                try:
                    return self._send_asset("browser-approval-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "browser-approval-panel.js не найден"})
            if parsed.path == "/v1/browser/approvals":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                # API token сам по себе не устанавливает личность владельца.
                # Только серверная HttpOnly Web-сессия разрешает подтверждения.
                resolve_web_owner = getattr(self, "_existing_web_owner", None)
                owner_id = resolve_web_owner() if callable(resolve_web_owner) else ""
                if not owner_id or owner_id == "web:local":
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется Web-сессия"})
                args = parse.parse_qs(parsed.query)
                session_id = self._arg(args, "session_id")
                if not session_id:
                    return self._json(HTTPStatus.OK, {"items": []})
                try:
                    platform = _platform(app)
                    platform._require_owned_approval_session(owner_id, session_id)
                    return self._json(
                        HTTPStatus.OK,
                        {"items": platform.approvals.list(owner_id, session_id=session_id)},
                    )
                except (ValueError, PermissionError):
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Подтверждение не найдено"})
                except Exception as exc:
                    problem("browser_approval_list_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Ошибка подтверждения"})
            if parsed.path == "/v1/tools/extensions":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    items = [manifest.to_dict() for manifest in _platform(app).tools_registry.list()]
                    for item in items:
                        item.pop("env", None)
                    return self._json(HTTPStatus.OK, {"items": items})
                except Exception as exc:
                    problem("tools_extension_list_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/v1/browser/approvals/resolve":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                resolve_web_owner = getattr(self, "_existing_web_owner", None)
                owner_id = resolve_web_owner() if callable(resolve_web_owner) else ""
                if not owner_id or owner_id == "web:local":
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется Web-сессия"})
                try:
                    body = self._body()
                    session_id = str(body.get("session_id") or "").strip()
                    approval_id = str(body.get("approval_id") or "").strip()
                    approved = body.get("approved")
                    if not session_id or not approval_id or not isinstance(approved, bool):
                        raise ValueError("Нужны session_id, approval_id и approved=true/false")
                    result = _platform(app).resolve_approval(
                        owner_id, approval_id, session_id=session_id, approved=approved
                    )
                    return self._json(
                        HTTPStatus.OK,
                        {"status": result.status, "message": result.message, "data": result.data},
                    )
                except (KeyError, ValueError, PermissionError):
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Подтверждение не найдено"})
                except Exception as exc:
                    problem("browser_approval_resolve_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Ошибка подтверждения"})

            if parsed.path == "/v1/documents/convert":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    path = str(body.get("path") or "").strip()
                    if not path:
                        raise ValueError("Нужен локальный path")
                    result = _platform(app).documents.convert(path)
                    return self._json(
                        HTTPStatus.OK,
                        {
                            "path": result.path,
                            "markdown": result.markdown,
                            "engine": result.engine,
                            "size_bytes": result.size_bytes,
                            "metadata": result.metadata,
                        },
                    )
                except (ValueError, FileNotFoundError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("document_intelligence_http_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/tools/extensions/inspect":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    entry = MarketplaceEntry.from_dict(dict(body.get("entry") or body))
                    return self._json(HTTPStatus.OK, _platform(app).marketplace.inspect(entry))
                except (ValueError, TypeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("extension_inspect_http_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/tools/extensions/install":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    approved = body.get("approved")
                    if approved is not True:
                        raise PermissionError("Установка расширения требует approved=true после показа разрешений")
                    entry = MarketplaceEntry.from_dict(dict(body.get("entry") or {}))
                    receipt = _platform(app).marketplace.install(entry, approved=True)
                    return self._json(HTTPStatus.OK, receipt)
                except (ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("extension_install_http_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/tools/extensions/uninstall":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    extension_id = str(body.get("extension_id") or "").strip()
                    if not extension_id or body.get("approved") is not True:
                        raise PermissionError("Удаление расширения требует extension_id и approved=true")
                    _platform(app).marketplace.uninstall(extension_id, approved=True)
                    return self._json(HTTPStatus.OK, {"ok": True, "extension_id": extension_id})
                except (KeyError, ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("extension_uninstall_http_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/tools/call":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    tool_id = str(body.get("tool_id") or "").strip()
                    tool_name = str(body.get("tool_name") or "").strip()
                    if not tool_id or not tool_name:
                        raise ValueError("Нужны tool_id и tool_name")
                    result = _platform(app).tools_gateway.call(
                        tool_id,
                        tool_name,
                        dict(body.get("arguments") or {}),
                        approved=body.get("approved") is True,
                    )
                    return self._json(HTTPStatus.OK, result)
                except (KeyError, ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("mcp_tool_http_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

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
