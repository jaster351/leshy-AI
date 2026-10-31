from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse

from .app import LeshyApp, LLMUnavailable
from .config import Settings
from .dashboard import DashboardService
from .diagnostics import event, problem
from .hardware import HardwareConfig, HardwareConfigStore, hardware_status
from .model_catalog import (
    LocalModelCatalog,
    apply_saved_model_to_settings,
    configure_app_model_verifier,
    switch_app_model,
)
from .telegram import TelegramBridge


def _web_asset(settings: Settings, name: str) -> Path:
    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        candidates.append(Path(bundle) / "web" / name)
    candidates.append(settings.install_root / "web" / name)
    candidates.append(Path(__file__).resolve().parents[2] / "web" / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Не найден web/{name}")


def _web_index(settings: Settings) -> Path:
    return _web_asset(settings, "index.html")


def make_handler(
    app: LeshyApp,
    telegram: TelegramBridge,
    settings: Settings,
    model_catalog: LocalModelCatalog,
):
    dashboard = DashboardService(app)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Leshy/2"

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

        @staticmethod
        def _arg(args: dict[str, list[str]], name: str, default: str = "") -> str:
            return str((args.get(name) or [default])[0]).strip()

        def _send_asset(self, path: Path, content_type: str) -> None:
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = parse.urlparse(self.path)
            args = parse.parse_qs(parsed.query)
            if parsed.path == "/":
                html = _web_index(settings).read_text(encoding="utf-8")
                css_marker = '<link rel="stylesheet" href="/variant3.css">'
                if css_marker not in html:
                    html = html.replace("</head>", css_marker + "\n</head>")
                markers = (
                    '<script src="/ui-enhancements.js"></script>',
                    '<script src="/hardware-panel.js"></script>',
                    '<script src="/variant3.js"></script>',
                )
                scripts = "".join(marker + "\n" for marker in markers if marker not in html)
                if scripts:
                    html = html.replace("</body>", scripts + "</body>")
                data = html.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/ui-enhancements.js":
                self._send_asset(_web_asset(settings, "ui-enhancements.js"), "application/javascript; charset=utf-8")
                return
            if parsed.path == "/hardware-panel.js":
                self._send_asset(_web_asset(settings, "hardware-panel.js"), "application/javascript; charset=utf-8")
                return
            if parsed.path == "/variant3.js":
                self._send_asset(_web_asset(settings, "variant3.js"), "application/javascript; charset=utf-8")
                return
            if parsed.path == "/variant3.css":
                self._send_asset(_web_asset(settings, "variant3.css"), "text/css; charset=utf-8")
                return
            if parsed.path.startswith("/generated/"):
                target = app.image_generation.resolve_generated(parsed.path.rsplit("/", 1)[-1])
                if not target:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data = target.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return
            if parsed.path == "/v1/status":
                self._json(HTTPStatus.OK, app.status())
                return
            if parsed.path == "/v1/hardware":
                self._json(HTTPStatus.OK, hardware_status(settings.brain_dir))
                return
            if parsed.path == "/v1/overview":
                self._json(HTTPStatus.OK, dashboard.overview(self._arg(args, "owner_id", "web:local")))
                return
            if parsed.path == "/v1/model/status":
                self._json(HTTPStatus.OK, app.model_downloader.status())
                return
            if parsed.path == "/v1/models":
                self._json(HTTPStatus.OK, model_catalog.status(active_path=app.model_path))
                return
            if parsed.path == "/v1/image/status":
                self._json(HTTPStatus.OK, app.image_generation.status())
                return
            if parsed.path == "/v1/image/job":
                job_id = self._arg(args, "job_id")
                job = app.image_generation.job(job_id)
                if not job:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Задача генерации изображения не найдена"})
                else:
                    self._json(HTTPStatus.OK, job)
                return
            if parsed.path == "/v1/telegram/status":
                self._json(HTTPStatus.OK, telegram.status())
                return
            if parsed.path == "/v1/history":
                session_id = self._arg(args, "session_id")
                owner_id = self._arg(args, "owner_id", "web:local")
                if not session_id:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "session_id обязателен"})
                    return
                try:
                    self._json(HTTPStatus.OK, {"items": app.history(owner_id, session_id)})
                except Exception as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if parsed.path == "/v1/sessions":
                self._json(HTTPStatus.OK, {"items": dashboard.sessions(self._arg(args, "owner_id", "web:local"), self._arg(args, "limit", "50"))})
                return
            if parsed.path == "/v1/memories":
                self._json(HTTPStatus.OK, {"items": dashboard.memories(self._arg(args, "owner_id", "web:local"), self._arg(args, "limit", "100"))})
                return
            if parsed.path == "/v1/knowledge":
                self._json(HTTPStatus.OK, {"items": dashboard.knowledge(self._arg(args, "status", "all"), self._arg(args, "limit", "100"))})
                return
            if parsed.path == "/v1/research/tasks":
                self._json(HTTPStatus.OK, {"items": dashboard.research_tasks(self._arg(args, "limit", "100"))})
                return
            if parsed.path == "/v1/learning/history":
                self._json(HTTPStatus.OK, {"items": dashboard.learning_history(self._arg(args, "limit", "100"))})
                return
            if parsed.path == "/v1/tasks":
                self._json(HTTPStatus.OK, {"items": dashboard.tasks(self._arg(args, "owner_id", "web:local"), self._arg(args, "status", "all"), self._arg(args, "limit", "100"))})
                return
            if parsed.path == "/v1/capabilities":
                self._json(HTTPStatus.OK, {"items": dashboard.capabilities()})
                return
            if parsed.path == "/v1/logs":
                self._json(HTTPStatus.OK, dashboard.logs(self._arg(args, "limit", "120")))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Не найдено"})

        def _upload_model(self) -> None:
            raw_name = self.headers.get("X-Leshy-Filename", "").strip()
            filename = parse.unquote(raw_name)
            if not filename:
                raise ValueError("Не передано имя GGUF-файла")
            length = int(self.headers.get("Content-Length", "0") or "0")
            result = model_catalog.save_upload(self.rfile, length=length, filename=filename)
            payload = model_catalog.status(active_path=app.model_path)
            payload["uploaded"] = result
            self._json(HTTPStatus.CREATED, payload)

        def do_POST(self):
            if not self._authorized():
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return
            try:
                # GGUF-файлы могут занимать десятки гигабайт, поэтому этот endpoint
                # читает тело потоково и не использует 2-МБ JSON parser.
                if self.path == "/v1/models/upload":
                    self._upload_model()
                    return

                body = self._body()
                if self.path == "/v1/chat":
                    result = app.chat(
                        str(body.get("message") or ""),
                        owner_id=str(body.get("owner_id") or "web:local"),
                        session_id=str(body.get("session_id") or "") or None,
                    )
                    self._json(HTTPStatus.OK, result)
                    return
                if self.path == "/v1/hardware/config":
                    store = HardwareConfigStore(settings.brain_dir)
                    current = store.load()
                    config = HardwareConfig(
                        mode=str(body.get("mode") or current.mode).strip().lower(),
                        gpu_layers_mixed=int(body.get("gpu_layers_mixed", current.gpu_layers_mixed)),
                        prefer_npu_for_background=bool(body.get("prefer_npu_for_background", current.prefer_npu_for_background)),
                    )
                    store.save(config)
                    self._json(HTTPStatus.OK, hardware_status(settings.brain_dir))
                    return
                if self.path == "/v1/image/generate":
                    job = app.image_generation.enqueue(
                        str(body.get("prompt") or ""),
                        width=int(body.get("width") or 512),
                        height=int(body.get("height") or 512),
                        steps=int(body.get("steps") or 20),
                    )
                    self._json(HTTPStatus.ACCEPTED, job)
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
                if self.path == "/v1/memory":
                    owner_id = str(body.get("owner_id") or "web:local")
                    memory_id = app.remember(owner_id, str(body.get("content") or ""), importance=float(body.get("importance") or .8))
                    self._json(HTTPStatus.CREATED, {"id": memory_id})
                    return
                if self.path == "/v1/tasks":
                    task = dashboard.create_task(
                        owner_id=str(body.get("owner_id") or "web:local"),
                        title=str(body.get("title") or ""),
                        details=str(body.get("details") or ""),
                        priority=int(body.get("priority") or 0),
                        due_at=str(body.get("due_at") or "") or None,
                    )
                    self._json(HTTPStatus.CREATED, task)
                    return
                if self.path == "/v1/tasks/update":
                    task_id = int(body.get("id") or 0)
                    if task_id <= 0:
                        raise ValueError("id задачи обязателен")
                    changes = {key: body[key] for key in ("title", "details", "status", "priority", "due_at") if key in body}
                    task = dashboard.update_task(owner_id=str(body.get("owner_id") or "web:local"), task_id=task_id, **changes)
                    self._json(HTTPStatus.OK, task)
                    return
                if self.path == "/v1/model/download":
                    self._json(HTTPStatus.ACCEPTED, app.model_downloader.start())
                    return
                if self.path == "/v1/models/select":
                    self._json(HTTPStatus.OK, switch_app_model(app, model_catalog, str(body.get("name") or "")))
                    return
                if self.path == "/v1/telegram/config":
                    users = body.get("allowed_users") or []
                    if isinstance(users, str):
                        users = [part.strip() for part in users.replace(",", "\n").splitlines() if part.strip()]
                    telegram.configure(
                        str(body.get("token") or ""),
                        users,
                        allow_all=bool(body.get("allow_all", False)),
                        owner_id=str(body.get("owner_id") or ""),
                    )
                    telegram.start()
                    self._json(HTTPStatus.OK, telegram.status())
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "Не найдено"})
            except LLMUnavailable as exc:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "model_unavailable"})
            except (ValueError, PermissionError, FileNotFoundError, FileExistsError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Внутренняя ошибка: {exc}"})

        def do_OPTIONS(self):
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

    return Handler


def _warm_local_llm(app: LeshyApp) -> None:
    warmer = getattr(app.llm, "warmup", None)
    if not callable(warmer):
        return
    try:
        backend = warmer()
        event("llm_background_warmup_finished", backend=backend or "none")
    except Exception as exc:
        problem("llm_background_warmup_failed", str(exc))


def serve(host: str | None = None, port: int | None = None) -> None:
    settings = Settings.from_env()
    model_catalog = apply_saved_model_to_settings(settings)
    app = LeshyApp(settings)
    configure_app_model_verifier(app, model_catalog)
    telegram = TelegramBridge(app, settings.brain_dir)
    app.telegram = telegram
    app.start_background()
    threading.Thread(target=_warm_local_llm, args=(app,), name="leshy-llm-warmup", daemon=True).start()
    telegram.start()
    bind_host = host or os.getenv("LESHY_HOST", "0.0.0.0")
    bind_port = int(port or os.getenv("LESHY_PORT", "8765"))
    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(app, telegram, settings, model_catalog))
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
