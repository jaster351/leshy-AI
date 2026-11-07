from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse

from .app import LeshyApp, LLMUnavailable
from .config import Settings
from .dashboard import DashboardService
from .diagnostics import event, problem
from .hardware import HardwareConfig, HardwareConfigStore, hardware_status
from .model_catalog import LocalModelCatalog, apply_saved_model_to_settings, configure_app_model_verifier, switch_app_model
from .model_library import ModelLibraryManager
from .nas_settings import NasSettings, NasSettingsStore
from .realtime_chat import RealtimeChatManager
from .runtime_compat import CompatibleRuntimeManager, RuntimePolicy, RuntimePolicyStore
from .session_admin import SessionAdminService
from .startup_recovery import recover_unanswered
from .streaming_backends import install_streaming_backends
from .telegram_rich import TelegramBridge


def _web_asset(settings: Settings, name: str) -> Path:
    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        candidates.append(Path(bundle) / "web" / name)
    candidates.extend((settings.install_root / "web" / name, Path(__file__).resolve().parents[2] / "web" / name))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Не найден web/{name}")


def _web_index(settings: Settings) -> Path:
    return _web_asset(settings, "index.html")


def _inject_runtime_ui(html: str) -> str:
    css_marker = '<link rel="stylesheet" href="/variant3.css">'
    if css_marker not in html:
        html = html.replace("</head>", css_marker + "\n</head>")
    markers = (
        '<script src="/ui-enhancements.js"></script>',
        '<script src="/hardware-panel.js"></script>',
        '<script src="/model-panel.js"></script>',
        '<script src="/model-library-panel.js"></script>',
        '<script src="/nas-panel.js"></script>',
        '<script src="/variant3.js"></script>',
        '<script src="/chat-extras.js"></script>',
    )
    scripts = "".join(marker + "\n" for marker in markers if marker not in html)
    if scripts:
        html = html.replace("</body>", scripts + "</body>")

    refresh_button = '<button class="button small" id="logsRefresh">Обновить</button>'
    clear_button = '<button class="button danger small" id="logsClearManual" type="button">Очистить логи</button>'
    if "logsClearManual" not in html and refresh_button in html:
        html = html.replace(refresh_button, refresh_button + clear_button)

    controls = r'''
<script id="leshy-runtime-ui-controls">
document.addEventListener('DOMContentLoaded',function(){
  var button=document.getElementById('logsClearManual');
  if(!button||button.dataset.bound==='1')return;
  button.dataset.bound='1';
  button.addEventListener('click',async function(){
    if(!window.confirm('Очистить runtime-логи Лешего? История чатов, знания, настройки и модели не будут удалены.'))return;
    button.disabled=true;var old=button.textContent;button.textContent='Очищаю…';
    try{
      var response=await fetch('/v1/logs/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      var data=await response.json().catch(function(){return {error:'Некорректный ответ сервера'};});
      if(!response.ok)throw new Error(data.error||('HTTP '+response.status));
      if(typeof window.loadLogs==='function')await window.loadLogs();
      window.alert('Логи очищены. Обработано файлов: '+String(data.cleared_files||0));
    }catch(error){window.alert('Не удалось очистить логи: '+error.message);}
    finally{button.disabled=false;button.textContent=old;}
  });
});
</script>
'''
    if "leshy-runtime-ui-controls" not in html:
        html = html.replace("</body>", controls + "</body>")
    return html


def make_handler(app: LeshyApp, telegram: TelegramBridge, settings: Settings, model_catalog: LocalModelCatalog):
    dashboard = DashboardService(app)
    nas_store = NasSettingsStore(settings.brain_dir)
    model_library = ModelLibraryManager(settings.native_models_dir)
    runtime_store = RuntimePolicyStore(settings.brain_dir)
    session_admin = SessionAdminService(app.storage)
    realtime = RealtimeChatManager(app, session_admin=session_admin)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Leshy/2"

        def log_message(self, fmt: str, *args):
            return

        def _authorized(self) -> bool:
            expected = os.getenv("LESHY_API_TOKEN", "").strip()
            return not expected or self.headers.get("Authorization", "") == f"Bearer {expected}"

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

        def _send_asset(self, name: str, content_type: str) -> None:
            data = _web_asset(settings, name).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _stream_chat(self, body: dict) -> None:
            request_id = str(body.get("request_id") or "").strip() or None
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                for item in realtime.stream(
                    str(body.get("message") or ""),
                    owner_id=str(body.get("owner_id") or "web:local"),
                    session_id=str(body.get("session_id") or "") or None,
                    request_id=request_id,
                ):
                    payload = json.dumps(item.data, ensure_ascii=False, separators=(",", ":"))
                    packet = f"event: {item.type}\ndata: {payload}\n\n".encode("utf-8")
                    self.wfile.write(packet)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                if request_id:
                    realtime.stop(request_id)
            finally:
                self.close_connection = True

        def do_GET(self):
            parsed = parse.urlparse(self.path)
            args = parse.parse_qs(parsed.query)
            path = parsed.path
            if path == "/":
                data = _inject_runtime_ui(_web_index(settings).read_text(encoding="utf-8")).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers(); self.wfile.write(data); return

            assets = {
                "/ui-enhancements.js": ("ui-enhancements.js", "application/javascript; charset=utf-8"),
                "/hardware-panel.js": ("hardware-panel.js", "application/javascript; charset=utf-8"),
                "/model-panel.js": ("model-panel.js", "application/javascript; charset=utf-8"),
                "/model-library-panel.js": ("model-library-panel.js", "application/javascript; charset=utf-8"),
                "/nas-panel.js": ("nas-panel.js", "application/javascript; charset=utf-8"),
                "/variant3.js": ("variant3.js", "application/javascript; charset=utf-8"),
                "/chat-extras.js": ("chat-extras.js", "application/javascript; charset=utf-8"),
                "/variant3.css": ("variant3.css", "text/css; charset=utf-8"),
            }
            if path in assets:
                name, content_type = assets[path]; self._send_asset(name, content_type); return
            if path.startswith("/generated/"):
                target = app.image_generation.resolve_generated(path.rsplit("/", 1)[-1])
                if not target: self.send_error(HTTPStatus.NOT_FOUND); return
                data = target.read_bytes(); self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data); return
            if not self._authorized(): self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"}); return
            if path == "/v1/status": return self._json(HTTPStatus.OK, app.status())
            if path == "/v1/hardware": return self._json(HTTPStatus.OK, hardware_status(settings.brain_dir))
            if path == "/v1/overview": return self._json(HTTPStatus.OK, dashboard.overview(self._arg(args, "owner_id", "web:local")))
            if path == "/v1/model/status": return self._json(HTTPStatus.OK, app.model_downloader.status())
            if path == "/v1/models": return self._json(HTTPStatus.OK, model_catalog.status(active_path=app.model_path))
            if path == "/v1/model-library": return self._json(HTTPStatus.OK, model_library.status())
            if path == "/v1/runtime-policy":
                runtime_status = app.accelerator_runtime.status() if callable(getattr(app.accelerator_runtime, "status", None)) else {"mode": runtime_store.load().mode}
                return self._json(HTTPStatus.OK, runtime_status)
            if path == "/v1/nas": return self._json(HTTPStatus.OK, nas_store.public_status(probe=False))
            if path == "/v1/image/status": return self._json(HTTPStatus.OK, app.image_generation.status())
            if path == "/v1/image/job":
                job = app.image_generation.job(self._arg(args, "job_id")); return self._json(HTTPStatus.OK if job else HTTPStatus.NOT_FOUND, job or {"error": "Задача генерации изображения не найдена"})
            if path == "/v1/telegram/status": return self._json(HTTPStatus.OK, telegram.status())
            if path == "/v1/history":
                sid = self._arg(args, "session_id"); owner = self._arg(args, "owner_id", "web:local")
                if not sid: return self._json(HTTPStatus.BAD_REQUEST, {"error": "session_id обязателен"})
                return self._json(HTTPStatus.OK, {"items": app.history(owner, sid), "session": session_admin.get(owner, sid)})
            if path == "/v1/sessions":
                archived = self._arg(args, "archived", "0").lower() in {"1", "true", "yes"}
                return self._json(HTTPStatus.OK, {"items": session_admin.list(self._arg(args, "owner_id", "web:local"), archived=archived, limit=self._arg(args, "limit", "100")), "archived": archived})
            if path == "/v1/memories": return self._json(HTTPStatus.OK, {"items": dashboard.memories(self._arg(args, "owner_id", "web:local"), self._arg(args, "limit", "100"))})
            if path == "/v1/knowledge": return self._json(HTTPStatus.OK, {"items": dashboard.knowledge(self._arg(args, "status", "all"), self._arg(args, "limit", "100"))})
            if path == "/v1/research/tasks": return self._json(HTTPStatus.OK, {"items": dashboard.research_tasks(self._arg(args, "limit", "100"))})
            if path == "/v1/learning/history": return self._json(HTTPStatus.OK, {"items": dashboard.learning_history(self._arg(args, "limit", "100"))})
            if path == "/v1/tasks": return self._json(HTTPStatus.OK, {"items": dashboard.tasks(self._arg(args, "owner_id", "web:local"), self._arg(args, "status", "all"), self._arg(args, "limit", "100"))})
            if path == "/v1/capabilities": return self._json(HTTPStatus.OK, {"items": dashboard.capabilities()})
            if path == "/v1/logs": return self._json(HTTPStatus.OK, dashboard.logs(self._arg(args, "limit", "120")))
            self._json(HTTPStatus.NOT_FOUND, {"error": "Не найдено"})

        def _upload_model(self) -> None:
            filename = parse.unquote(self.headers.get("X-Leshy-Filename", "").strip())
            if not filename: raise ValueError("Не передано имя GGUF-файла")
            length = int(self.headers.get("Content-Length", "0") or "0")
            result = model_catalog.save_upload(self.rfile, length=length, filename=filename)
            payload = model_catalog.status(active_path=app.model_path); payload["uploaded"] = result; self._json(HTTPStatus.CREATED, payload)

        def do_POST(self):
            if not self._authorized(): self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"}); return
            try:
                if self.path == "/v1/models/upload": return self._upload_model()
                body = self._body(); path = self.path
                if path == "/v1/chat/stream": return self._stream_chat(body)
                if path == "/v1/chat/stop": return self._json(HTTPStatus.OK, {"stopped": realtime.stop(str(body.get("request_id") or ""))})
                if path == "/v1/chat":
                    owner = str(body.get("owner_id") or "web:local"); sid = str(body.get("session_id") or "") or None
                    if sid and session_admin.is_locked(owner, sid): raise PermissionError("Этот чат заблокирован. Разблокируйте его в меню чата.")
                    return self._json(HTTPStatus.OK, app.chat(str(body.get("message") or ""), owner_id=owner, session_id=sid))
                if path == "/v1/sessions/update":
                    owner = str(body.get("owner_id") or "web:local"); sid = str(body.get("id") or "").strip()
                    changes = {key: body[key] for key in ("title", "pinned", "archived", "locked") if key in body}
                    return self._json(HTTPStatus.OK, session_admin.update(owner, sid, **changes))
                if path == "/v1/sessions/delete": return self._json(HTTPStatus.OK, session_admin.delete(str(body.get("owner_id") or "web:local"), str(body.get("id") or "")))
                if path == "/v1/hardware/config":
                    store = HardwareConfigStore(settings.brain_dir); current = store.load()
                    config = HardwareConfig(mode=str(body.get("mode") or current.mode).strip().lower(), gpu_layers_mixed=int(body.get("gpu_layers_mixed", current.gpu_layers_mixed)), prefer_npu_for_background=bool(body.get("prefer_npu_for_background", current.prefer_npu_for_background)))
                    store.save(config); return self._json(HTTPStatus.OK, hardware_status(settings.brain_dir))
                if path == "/v1/runtime-policy":
                    saved = runtime_store.save(RuntimePolicy(mode=str(body.get("mode") or "auto")))
                    runtime_status = app.accelerator_runtime.status() if callable(getattr(app.accelerator_runtime, "status", None)) else {"mode": saved.mode}
                    return self._json(HTTPStatus.OK, runtime_status)
                if path == "/v1/model-library/download": return self._json(HTTPStatus.ACCEPTED, model_library.download(str(body.get("model_id") or ""), include_vision=bool(body.get("include_vision", True))))
                if path == "/v1/nas/config":
                    current = nas_store.load()
                    config = NasSettings(root=str(body.get("root", current.root) or ""), torrent_watch=str(body.get("torrent_watch", current.torrent_watch) or ""), music=str(body.get("music", current.music) or ""), video=str(body.get("video", current.video) or ""), smb_server=str(body.get("smb_server", current.smb_server) or ""), smb_username=str(body.get("smb_username", current.smb_username) or ""), smb_domain=str(body.get("smb_domain", current.smb_domain) or ""))
                    supplied = "password" in body and bool(str(body.get("password") or "")); saved = nas_store.save(config, password=str(body.get("password") or "") if supplied else None)
                    if bool(body.get("clear_password")) or not saved.smb_username: nas_store.clear_password()
                    return self._json(HTTPStatus.OK, nas_store.public_status(probe=False))
                if path == "/v1/nas/test": return self._json(HTTPStatus.OK, nas_store.public_status(probe=True))
                if path == "/v1/logs/clear": return self._json(HTTPStatus.OK, dashboard.clear_logs())
                if path == "/v1/image/generate": return self._json(HTTPStatus.ACCEPTED, app.image_generation.enqueue(str(body.get("prompt") or ""), width=int(body.get("width") or 512), height=int(body.get("height") or 512), steps=int(body.get("steps") or 20)))
                if path == "/v1/search": return self._json(HTTPStatus.OK, app.live_search(str(body.get("query") or ""), limit=int(body.get("limit") or 5)))
                if path == "/v1/research/queue":
                    task_id = app.queue_research(str(body.get("query") or "")); return self._json(HTTPStatus.ACCEPTED, {"task_id": task_id, "queued": True})
                if path == "/v1/learning": return self._json(HTTPStatus.OK, {"enabled": app.set_learning(bool(body.get("enabled")))})
                if path == "/v1/memory": return self._json(HTTPStatus.CREATED, {"id": app.remember(str(body.get("owner_id") or "web:local"), str(body.get("content") or ""), importance=float(body.get("importance") or .8))})
                if path == "/v1/tasks": return self._json(HTTPStatus.CREATED, dashboard.create_task(owner_id=str(body.get("owner_id") or "web:local"), title=str(body.get("title") or ""), details=str(body.get("details") or ""), priority=int(body.get("priority") or 0), due_at=str(body.get("due_at") or "") or None))
                if path == "/v1/tasks/update":
                    task_id = int(body.get("id") or 0)
                    if task_id <= 0: raise ValueError("id задачи обязателен")
                    changes = {key: body[key] for key in ("title", "details", "status", "priority", "due_at") if key in body}
                    return self._json(HTTPStatus.OK, dashboard.update_task(owner_id=str(body.get("owner_id") or "web:local"), task_id=task_id, **changes))
                if path == "/v1/model/download": return self._json(HTTPStatus.ACCEPTED, app.model_downloader.start())
                if path == "/v1/models/select":
                    result = switch_app_model(app, model_catalog, str(body.get("name") or "")); install_streaming_backends(app.llm); return self._json(HTTPStatus.OK, result)
                if path == "/v1/telegram/config":
                    users = body.get("allowed_users") or []
                    if isinstance(users, str): users = [part.strip() for part in users.replace(",", "\n").splitlines() if part.strip()]
                    telegram.configure(str(body.get("token") or ""), users, allow_all=bool(body.get("allow_all", False)), owner_id=str(body.get("owner_id") or "")); telegram.start(); return self._json(HTTPStatus.OK, telegram.status())
                self._json(HTTPStatus.NOT_FOUND, {"error": "Не найдено"})
            except LLMUnavailable as exc: self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc), "code": "model_unavailable"})
            except (ValueError, PermissionError, FileNotFoundError, FileExistsError, KeyError, json.JSONDecodeError) as exc: self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc: self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Внутренняя ошибка: {exc}"})

        def do_OPTIONS(self): self.send_response(HTTPStatus.NO_CONTENT); self.end_headers()

    return Handler


def _warm_local_llm(app: LeshyApp) -> None:
    warmer = getattr(app.llm, "warmup", None)
    if not callable(warmer): return
    try: event("llm_background_warmup_finished", backend=warmer() or "none")
    except Exception as exc: problem("llm_background_warmup_failed", str(exc))


def _delayed_recovery(app: LeshyApp, telegram: TelegramBridge, delay_seconds: float = 30.0) -> None:
    time.sleep(max(0.0, float(delay_seconds)))
    try: telegram.start()
    except Exception as exc: problem("telegram_delayed_start_failed", str(exc))
    try:
        result = recover_unanswered(app); event("startup_recovery_cycle_finished", delay_seconds=delay_seconds, **result)
    except Exception as exc: problem("startup_recovery_cycle_failed", str(exc), delay_seconds=delay_seconds)


def serve(host: str | None = None, port: int | None = None) -> None:
    settings = Settings.from_env(); model_catalog = apply_saved_model_to_settings(settings); app = LeshyApp(settings)
    app.accelerator_runtime = CompatibleRuntimeManager(settings.brain_dir, model_name_provider=lambda: app.model_path.name)
    app.llm = app._build_llm(); configure_app_model_verifier(app, model_catalog); install_streaming_backends(app.llm)
    telegram = TelegramBridge(app, settings.brain_dir); app.telegram = telegram; app.start_background()
    threading.Thread(target=_warm_local_llm, args=(app,), name="leshy-llm-warmup", daemon=True).start()
    threading.Thread(target=_delayed_recovery, args=(app, telegram, 30.0), name="leshy-startup-recovery", daemon=True).start()
    bind_host = host or os.getenv("LESHY_HOST", "0.0.0.0"); bind_port = int(port or os.getenv("LESHY_PORT", "8765"))
    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(app, telegram, settings, model_catalog))
    print(f"Леший {app.status()['version']} запущен: http://{bind_host}:{bind_port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: telegram.stop(); app.stop_background(); server.server_close()


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
