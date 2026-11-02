from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl9 as previous
from .advanced_modules_runtime import install_advanced_modules
from .app import LeshyApp
from .diagnostics import problem
from .module_settings import module_enabled


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    install_advanced_modules(LeshyApp, app)
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)

    class Handler(Parent):
        def do_GET(self):
            parsed = parse.urlparse(self.path)
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
