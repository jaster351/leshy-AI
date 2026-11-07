from __future__ import annotations

"""Кластерная репликация знаний поверх существующей LAN HMAC-аутентификации."""

from http import HTTPStatus
from urllib import parse

from . import web_server_impl11 as previous
from .cluster_knowledge_sync import KnowledgeSyncService
from .diagnostics import problem

_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    cluster = app.distributed_cluster
    service = getattr(app, "cluster_knowledge_sync", None)
    if not isinstance(service, KnowledgeSyncService):
        service = KnowledgeSyncService(app, cluster)
        app.cluster_knowledge_sync = service
        original_stop = app.stop_background

        def stop_with_sync():
            service.stop()
            original_stop()

        app.stop_background = stop_with_sync
        service.start()

    class Handler(Parent):
        def do_GET(self):
            if parse.urlsplit(self.path).path == "/v1/cluster/knowledge-sync/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return self._json(HTTPStatus.OK, service.status())
            return super().do_GET()

        def do_POST(self):
            if parse.urlsplit(self.path).path == "/v1/cluster/knowledge-sync/pull":
                try:
                    body = self._body()
                    return self._json(HTTPStatus.OK, service.handle_pull(body, self.headers))
                except PermissionError as exc:
                    return self._json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
                except ValueError as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("cluster_knowledge_sync_pull_failed", type(exc).__name__)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Синхронизация недоступна"})
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
