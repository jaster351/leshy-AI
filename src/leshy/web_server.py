from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse

from .app import LeshyApp, LLMUnavailable
from .config import Settings
from .telegram import TelegramBridge


def _web_index(settings: Settings) -> Path:
    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        candidates.append(Path(bundle) / "web" / "index.html")
    candidates.append(settings.install_root / "web" / "index.html")
    candidates.append(Path(__file__).resolve().parents[2] / "web" / "index.html")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Не найден web/index.html")


def make_handler(app: LeshyApp, telegram: TelegramBridge, settings: Settings):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Leshy/1"

        def log_message(self, fmt: str, *args):
            return

        def _authorized(self) -> bool:
            expected = os.getenv("LESHY_API_TOKEN", "").strip()
            if not expected:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {expected}"

        def _json(self, status: int, payload: dict | list):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 2_000_000:
                raise ValueError("Слишком большой запрос")
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Ожидается JSON-объект")
            return value

        def do_GET(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/":
                data = _web_index(settings).read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return
            if parsed.path == "/v1/status":
                self._json(HTTPStatus.OK, app.status())
                return
            if parsed.path == "/v1/model/status":
                self._json(HTTPStatus.OK, app.model_downloader.status())
                return
            if parsed.path == "/v1/telegram/status":
                self._json(HTTPStatus.OK, telegram.status())
                return
            if parsed.path == "/v1/history":
                args = parse.parse_qs(parsed.query)
                session_id = str((args.get("session_id") or [""])[0]).strip()
                owner_id = str((args.get("owner_id") or ["web:local"])[0]).strip()
                if not session_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "session_id обязателен"})
                    return
                try:
                    self._json(HTTPStatus.OK, {"items": app.history(owner_id, session_id)})
                except Exception as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Не найдено"})

        def do_POST(self):
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return
            try:
                body = self._body()
                if self.path == "/v1/chat":
                    result = app.chat(
                        str(body.get("message") or ""),
                        owner_id=str(body.get("owner_id") or "web:local"),
                        session_id=str(body.get("session_id") or "") or None,
                    )
                    self._json(HTTPStatus.OK, result)
                    return
                if self.path == "/v1/search":
                    self._json(HTTPStatus.OK, app.live_search(str(body.get("query") or ""), limit=int(body.get("limit") or 5)))
                    return
                if self.path == "/v1/research/queue":
                    task_id = app.queue_research(str(body.get("query") or ""))
                    self._json(HTTPStatus.ACCEPTED, {"task_id": task_id, "queued": True})
                    return
                if self.path == "/v1/learning":
                    enabled = app.set_learning(bool(body.get("enabled")))
                    self._json(HTTPStatus.OK, {"enabled": enabled})
                    return
                if self.path == "/v1/model/download":
                    self._json(HTTPStatus.ACCEPTED, app.model_downloader.start())
                    return
                if self.path == "/v1/telegram/config":
                    users = body.get("allowed_users") or []
                    if isinstance(users, str):
                        users = [part.strip() for part in users.replace(",", "\n").splitlines() if part.strip()]
                    telegram.configure(str(body.get("token") or ""), users)
                    telegram.start()
                    self._json(HTTPStatus.OK, telegram.status())
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "Не найдено"})
            except LLMUnavailable as exc:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "model_unavailable"})
            except (ValueError, PermissionError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Внутренняя ошибка: {exc}"})

        def do_OPTIONS(self):
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    settings = Settings.from_env()
    app = LeshyApp(settings)
    telegram = TelegramBridge(app, settings.brain_dir)
    app.telegram = telegram
    app.start_background()
    telegram.start()
    bind_host = host or os.getenv("LESHY_HOST", "0.0.0.0")
    bind_port = int(port or os.getenv("LESHY_PORT", "8765"))
    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(app, telegram, settings))
    print(f"Леший {app.status()['version']} запущен: http://{bind_host}:{bind_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        telegram.stop()
        app.stop_background()
        server.server_close()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
