from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl9 as previous
from .advanced_modules_runtime import install_advanced_modules
from .app import LeshyApp
from .diagnostics import problem
from .lenta_browser_session import LentaSessionStore
from .module_settings import module_enabled


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def _runtime_html(settings) -> bytes:
    html_text = previous._runtime_html(settings).decode("utf-8")
    marker = '<script src="/lenta-settings-panel.js"></script>'
    if marker not in html_text:
        html_text = html_text.replace("</body>", marker + "\n</body>")
    return html_text.encode("utf-8")


def _owner(value: object) -> str:
    owner = str(value or "web:local").strip()[:200]
    return owner or "web:local"


def _lenta_session(app, owner_id: str):
    return LentaSessionStore(app.settings.brain_dir).for_owner(owner_id)


def make_handler(app, telegram, settings, model_catalog):
    install_advanced_modules(LeshyApp, app)
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

            if parsed.path == "/lenta-settings-panel.js":
                try:
                    return self._send_asset("lenta-settings-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "lenta-settings-panel.js не найден"})

            if parsed.path == "/v1/lenta/settings":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                args = parse.parse_qs(parsed.query)
                owner_id = _owner((args.get("owner_id") or ["web:local"])[0])
                try:
                    return self._json(HTTPStatus.OK, _lenta_session(app, owner_id).status())
                except Exception as exc:
                    problem("lenta_settings_read_failed", str(exc), owner_id=owner_id)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/cluster/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    if not module_enabled(app.storage, "distributed-cluster"):
                        return self._json(HTTPStatus.OK, {"enabled": False, "peer_count": 0})
                    return self._json(HTTPStatus.OK, app.distributed_cluster.status())
                except Exception as exc:
                    problem("cluster_status_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/advanced/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                args = parse.parse_qs(parsed.query)
                owner = str((args.get("owner_id") or ["web:local"])[0])[:200]
                try:
                    return self._json(HTTPStatus.OK, {
                        "intelligent_memory": app.intelligent_memory.status(owner),
                        "knowledge_graph": app.knowledge_graph.status(owner),
                        "agent_orchestrator": app.multi_agent_orchestrator.status(),
                        "distributed_cluster": app.distributed_cluster.status() if module_enabled(app.storage, "distributed-cluster") else {"enabled": False},
                    })
                except Exception as exc:
                    problem("advanced_status_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)

            if parsed.path == "/v1/lenta/settings":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner_id = _owner(body.get("owner_id"))
                    session = _lenta_session(app, owner_id)
                    session.update_preferences(
                        delivery_mode=body.get("delivery_mode"),
                        payment_mode=body.get("payment_mode"),
                        substitution_policy=body.get("substitution_policy"),
                    )
                    return self._json(HTTPStatus.OK, session.status())
                except ValueError as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("lenta_settings_write_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/lenta/open-account":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner_id = _owner(body.get("owner_id"))
                    session = _lenta_session(app, owner_id)
                    session.open_login_window("https://lenta.com/")
                    return self._json(HTTPStatus.OK, session.status())
                except Exception as exc:
                    problem("lenta_open_account_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/lenta/mark-authenticated":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner_id = _owner(body.get("owner_id"))
                    session = _lenta_session(app, owner_id)
                    session.mark_authenticated()
                    return self._json(HTTPStatus.OK, session.status())
                except Exception as exc:
                    problem("lenta_mark_authenticated_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/cluster/task":
                if not module_enabled(app.storage, "distributed-cluster"):
                    return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Распределённый кластер выключен"})
                try:
                    body = self._body()
                    result = app.distributed_cluster.handle_remote_task(body, self.headers)
                    return self._json(HTTPStatus.OK, result)
                except PermissionError as exc:
                    return self._json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
                except ValueError as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("cluster_remote_task_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/cluster/refresh":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    if not module_enabled(app.storage, "distributed-cluster"):
                        return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Распределённый кластер выключен"})
                    app.distributed_cluster.refresh()
                    return self._json(HTTPStatus.OK, app.distributed_cluster.status())
                except Exception as exc:
                    problem("cluster_refresh_endpoint_failed", str(exc))
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
