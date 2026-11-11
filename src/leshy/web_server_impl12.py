from __future__ import annotations

"""Кластерная репликация знаний поверх существующей LAN HMAC-аутентификации."""

from http import HTTPStatus
from urllib import parse

from . import web_server_impl11 as previous
from .cluster_knowledge_sync import KnowledgeSyncService
from .diagnostics import problem
from .swarm_membership import SwarmMembership
from .search_parallel import search_provider_status
from .search_chromium import chromium_search_status

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

    swarm = getattr(app, "swarm_membership", None)
    if not isinstance(swarm, SwarmMembership):
        swarm = SwarmMembership(app)
        app.swarm_membership = swarm
        previous_stop = app.stop_background

        def stop_with_swarm():
            swarm.stop()
            previous_stop()

        app.stop_background = stop_with_swarm
        swarm.start()

    class Handler(Parent):
        def do_GET(self):
            if parse.urlsplit(self.path).path == "/v1/search/providers":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return self._json(HTTPStatus.OK, {
                    **search_provider_status(), "chromium": chromium_search_status(),
                })
            if parse.urlsplit(self.path).path == "/v1/swarm/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return self._json(HTTPStatus.OK, swarm.status())
            if parse.urlsplit(self.path).path == "/v1/cluster/knowledge-sync/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return self._json(HTTPStatus.OK, service.status())
            return super().do_GET()

        def do_POST(self):
            if parse.urlsplit(self.path).path == "/v1/swarm/settings":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    enabled = body.get("auto_join_enabled")
                    if type(enabled) is not bool:
                        raise ValueError("auto_join_enabled должен быть boolean")
                    swarm.set_enabled(enabled)
                    return self._json(HTTPStatus.OK, swarm.status())
                except (ValueError, AttributeError, TypeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
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
