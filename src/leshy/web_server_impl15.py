"""Owner-authenticated settings and pairing UI for Parser for Plex."""
from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl14 as previous
from .diagnostics import problem
from .torrent_parser_bridge import TorrentParserBridge

_PREVIOUS_MAKE_HANDLER = previous.make_handler


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    bridge = TorrentParserBridge(settings.brain_dir)

    class Handler(Parent):
        def do_GET(self):
            path = parse.urlsplit(self.path).path
            if path == "/torrent-parser-panel.js":
                try:
                    return self._send_asset("torrent-parser-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Панель Parser for Plex не найдена"})
            if path == "/v1/torrent-parser/config":
                if self._settings_owner() is None:
                    return
                return self._json(HTTPStatus.OK, bridge.status())
            return super().do_GET()

        def do_POST(self):
            path = parse.urlsplit(self.path).path
            actions = {
                "/v1/torrent-parser/config", "/v1/torrent-parser/pair",
                "/v1/torrent-parser/test",
            }
            if path not in actions:
                return super().do_POST()
            owner = self._settings_owner()
            if owner is None:
                return
            if self.headers.get("X-Leshy-UI") != "torrent-parser":
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Подтвердите действие в интерфейсе Лешего"})
            origin = str(self.headers.get("Origin") or "")
            if origin and parse.urlsplit(origin).netloc.lower() != str(self.headers.get("Host") or "").lower():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Недопустимый источник"})
            if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Требуется application/json"})
            try:
                payload = self._body()
                if path.endswith("/config"):
                    configured = bridge.configure(payload.get("address", ""), owner)
                    if not configured["paired"]:
                        try:
                            configured = bridge.pair(owner)
                        except (PermissionError, ConnectionError, ValueError, RuntimeError) as exc:
                            configured["pairing_error"] = str(exc)
                    return self._json(HTTPStatus.OK, configured)
                if path.endswith("/pair"):
                    return self._json(HTTPStatus.OK, bridge.pair(owner))
                if path.endswith("/test"):
                    result = bridge.test()
                    return self._json(HTTPStatus.OK, {"connected": bool(result.get("ok")),
                                                       "server": result.get("service", ""),
                                                       **bridge.status()})
            except (ValueError, PermissionError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except (ConnectionError, TimeoutError, RuntimeError, OSError) as exc:
                problem("torrent_parser_bridge_unavailable", type(exc).__name__)
                return self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

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
