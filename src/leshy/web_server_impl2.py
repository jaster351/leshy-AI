from __future__ import annotations

import os
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib import parse

from . import web_server_impl as base
from .dashboard import DashboardService
from .diagnostics import event, problem
from .model_catalog import apply_saved_model_to_settings, configure_app_model_verifier
from .runtime_compat import CompatibleRuntimeManager
from .streaming_backends import install_streaming_backends
from .telegram_rich import TelegramBridge


def make_handler(app, telegram, settings, model_catalog):
    Parent = base.make_handler(app, telegram, settings, model_catalog)
    dashboard = DashboardService(app)

    class Handler(Parent):
        def do_GET(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/":
                html = base._inject_runtime_ui(base._web_index(settings).read_text(encoding="utf-8"))
                marker = '<script src="/chatgpt-shell.js"></script>'
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

            if parsed.path == "/chatgpt-shell.js":
                try:
                    return self._send_asset("chatgpt-shell.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "chatgpt-shell.js не найден"})
                    return

            # Новый список сессий хранит pin/archive/lock самостоятельно, но
            # автоименование остаётся обязанностью DashboardService. Вызов ниже
            # только планирует одно безопасное фоновое переименование и не меняет
            # JSON-ответ расширенного endpoint.
            if parsed.path == "/v1/sessions":
                try:
                    args = parse.parse_qs(parsed.query)
                    owner = self._arg(args, "owner_id", "web:local")
                    limit = self._arg(args, "limit", "100")
                    dashboard.sessions(owner, limit)
                except Exception:
                    pass
            return super().do_GET()

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
    model_catalog = apply_saved_model_to_settings(settings)
    app = base.LeshyApp(settings)
    app.accelerator_runtime = CompatibleRuntimeManager(
        settings.brain_dir,
        model_name_provider=lambda: app.model_path.name,
    )
    app.llm = app._build_llm()
    configure_app_model_verifier(app, model_catalog)
    install_streaming_backends(app.llm)

    telegram = TelegramBridge(app, settings.brain_dir)
    app.telegram = telegram
    app.start_background()
    threading.Thread(target=_warm_local_llm, args=(app,), name="leshy-llm-warmup", daemon=True).start()
    threading.Thread(
        target=_delayed_recovery,
        args=(app, telegram, 30.0),
        name="leshy-startup-recovery",
        daemon=True,
    ).start()

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


__all__ = ["serve", "main", "make_handler"]
