from __future__ import annotations

import json
import os
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib import parse

from . import web_server_impl as base
from .dashboard import DashboardService
from .diagnostics import event, problem
from .hf_settings import HuggingFaceSettings
from .model_catalog import apply_saved_model_to_settings, configure_app_model_verifier
from .os_control_approval import install_os_control
from .runtime_compat import CompatibleRuntimeManager
from .streaming_backends import install_streaming_backends
from .telegram_fast import FastTelegramBridge
from .voice_asr import LocalVoiceASR
from .voice_tts import LocalVoiceTTS


def make_handler(app, telegram, settings, model_catalog):
    Parent = base.make_handler(app, telegram, settings, model_catalog)
    dashboard = DashboardService(app)
    voice_asr = LocalVoiceASR(settings.brain_dir)
    voice_tts = LocalVoiceTTS(settings.brain_dir)
    hf_settings = HuggingFaceSettings(settings.brain_dir)

    class Handler(Parent):
        def do_GET(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/":
                html = base._inject_runtime_ui(base._web_index(settings).read_text(encoding="utf-8"))
                for marker in (
                    '<script src="/chatgpt-shell.js"></script>',
                    '<script src="/right-menu-fix.js"></script>',
                    '<script src="/os-approval-ui.js"></script>',
                    '<script src="/hf-settings-panel.js"></script>',
                    '<script src="/thinking-indicator.js"></script>',
                    '<script src="/voice-live.js"></script>',
                ):
                    if marker not in html:
                        html = html.replace("</body>", marker + "\n</body>")
                data = html.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return

            if parsed.path in {"/chatgpt-shell.js", "/right-menu-fix.js", "/os-approval-ui.js", "/hf-settings-panel.js", "/thinking-indicator.js", "/voice-live.js"}:
                name = parsed.path.rsplit("/", 1)[-1]
                try:
                    return self._send_asset(name, "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": f"{name} не найден"})
                    return

            if parsed.path == "/v1/voice/status":
                if not self._authorized():
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                    return
                return self._json(HTTPStatus.OK, {"asr": voice_asr.status(), "tts": voice_tts.status()})

            if parsed.path == "/v1/os/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                controller = getattr(app, "os_control", None)
                return self._json(HTTPStatus.OK, controller.status() if controller else {"enabled": False})

            if parsed.path == "/v1/os/approvals":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                controller = getattr(app, "os_control", None)
                if controller is None or not callable(getattr(controller, "pending", None)):
                    return self._json(HTTPStatus.OK, {"items": []})
                args = parse.parse_qs(parsed.query)
                owner = self._arg(args, "owner_id", "web:local")
                session_id = self._arg(args, "session_id", "")
                return self._json(HTTPStatus.OK, {"items": controller.pending(owner_id=owner, session_id=session_id)})

            if parsed.path == "/v1/huggingface/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return self._json(HTTPStatus.OK, hf_settings.status())

            if parsed.path == "/v1/sessions":
                try:
                    args = parse.parse_qs(parsed.query)
                    owner = self._arg(args, "owner_id", "web:local")
                    limit = self._arg(args, "limit", "100")
                    dashboard.sessions(owner, limit)
                except Exception:
                    pass
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/v1/voice/transcribe":
                if not self._authorized():
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    if length <= 0:
                        raise ValueError("Пустая аудиозапись")
                    if length > 24 * 1024 * 1024:
                        raise ValueError("Голосовая запись слишком большая (максимум 24 МБ)")
                    audio = self.rfile.read(length)
                    args = parse.parse_qs(parsed.query)
                    language = str((args.get("language") or [""])[0]).strip() or None
                    result = voice_asr.transcribe(audio, content_type=self.headers.get("Content-Type", "audio/webm"), language=language)
                    return self._json(HTTPStatus.OK, result.as_dict())
                except (ValueError, RuntimeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc), "voice": voice_asr.status()})
                except Exception as exc:
                    problem("voice_http_transcribe_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Ошибка локального распознавания: {exc}"})

            if parsed.path == "/v1/voice/speak":
                if not self._authorized():
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                    return
                try:
                    body = self._body()
                    audio, meta = voice_tts.synthesize(str(body.get("text") or ""), preferred_voice=str(body.get("voice") or ""))
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(audio)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Leshy-TTS-Backend", str(meta.get("backend") or "local"))
                    if meta.get("voice"):
                        self.send_header("X-Leshy-TTS-Voice", str(meta["voice"]).encode("ascii", "ignore").decode("ascii"))
                    self.end_headers(); self.wfile.write(audio); return
                except (ValueError, RuntimeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc), "voice": voice_tts.status()})
                except Exception as exc:
                    problem("voice_http_speak_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Ошибка локальной озвучки: {exc}"})

            if parsed.path == "/v1/os/approval":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    controller = getattr(app, "os_control", None)
                    if controller is None or not callable(getattr(controller, "resolve", None)):
                        raise RuntimeError("Модуль подтверждения системных действий недоступен")
                    result = controller.resolve(str(body.get("id") or ""), owner_id=str(body.get("owner_id") or "web:local"), allow=bool(body.get("allow")))
                    return self._json(HTTPStatus.OK, result)
                except (ValueError, PermissionError, RuntimeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

            if parsed.path == "/v1/huggingface/config":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    hf_settings.save("" if bool(body.get("clear")) else str(body.get("token") or ""))
                    return self._json(HTTPStatus.OK, hf_settings.status())
                except Exception as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

            return super().do_POST()

    return Handler


def _warm_local_llm(app) -> None:
    warmer = getattr(app.llm, "warmup", None)
    if not callable(warmer):
        return
    try:
        event("llm_background_warmup_finished", backend=warmer() or "none")
    except Exception as exc:
        problem("llm_background_warmup_failed", str(exc))


def _delayed_recovery(app, telegram, delay_seconds: float = 30.0) -> None:
    time.sleep(max(0.0, float(delay_seconds)))
    try:
        telegram.start()
    except Exception as exc:
        problem("telegram_delayed_start_failed", str(exc))
    try:
        result = base.recover_unanswered(app)
        event("startup_recovery_cycle_finished", delay_seconds=delay_seconds, **result)
    except Exception as exc:
        problem("startup_recovery_cycle_failed", str(exc), delay_seconds=delay_seconds)


def serve(host: str | None = None, port: int | None = None) -> None:
    settings = base.Settings.from_env()
    HuggingFaceSettings(settings.brain_dir).apply()
    model_catalog = apply_saved_model_to_settings(settings)
    app = base.LeshyApp(settings)
    app.accelerator_runtime = CompatibleRuntimeManager(settings.brain_dir, model_name_provider=lambda: app.model_path.name)
    app.llm = app._build_llm()
    configure_app_model_verifier(app, model_catalog)
    install_streaming_backends(app.llm)
    install_os_control(app)

    telegram = FastTelegramBridge(app, settings.brain_dir)
    app.telegram = telegram
    app.start_background()
    threading.Thread(target=_warm_local_llm, args=(app,), name="leshy-llm-warmup", daemon=True).start()
    threading.Thread(target=_delayed_recovery, args=(app, telegram, 30.0), name="leshy-startup-recovery", daemon=True).start()

    bind_host = host or os.getenv("LESHY_HOST", "0.0.0.0")
    bind_port = int(port or os.getenv("LESHY_PORT", "8765"))
    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(app, telegram, settings, model_catalog))
    print(f"Леший {app.status()['version']} запущен: http://{bind_host}:{bind_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        telegram.stop(); app.stop_background(); server.server_close()


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
