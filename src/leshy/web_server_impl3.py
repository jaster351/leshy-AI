from __future__ import annotations

import os
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib import parse

from . import web_server_impl2 as previous
from .diagnostics import event, problem
from .hf_settings import HuggingFaceSettings
from .marketplace import MarketplaceManager
from .marketplace_chat import install_marketplace_chat_bridge
from .marketplace_plugin_runtime import MarketplacePluginRuntime
from .memory_policy import memory_status
from .model_catalog import apply_saved_model_to_settings, configure_app_model_verifier
from .os_control_approval import install_os_control
from .runtime_compat import CompatibleRuntimeManager
from .streaming_backends import install_streaming_backends
from .telegram_fast import FastTelegramBridge


def _apply_low_memory_guard(app, settings) -> None:
    """Не держать две CPU-копии одной GGUF на системах с небольшим объёмом RAM."""
    try:
        status = memory_status(settings.brain_dir)
        total = int((status.get("system") or {}).get("ram_total_bytes") or 0)
        states = list(getattr(app.llm, "_states", []) or [])
        if not total or total > 20 * 1024**3 or not states:
            return
        has_cpu_server = any(getattr(state, "name", "") == "cpu-llama-server" for state in states)
        if not has_cpu_server:
            return
        kept = [state for state in states if getattr(state, "name", "") != "cpu-native"]
        if len(kept) != len(states):
            app.llm._states = kept
            app.llm.model = "adaptive:" + ",".join(str(getattr(state, "name", "")) for state in kept)
            event("low_memory_cpu_native_disabled", ram_total_bytes=total, remaining_backends=[state.name for state in kept])
    except Exception as exc:
        problem("low_memory_guard_failed", str(exc))


def make_handler(app, telegram, settings, model_catalog):
    marketplace = getattr(app, "marketplace", None)
    if marketplace is None:
        marketplace = MarketplaceManager(settings.brain_dir)
        app.marketplace = marketplace
        app.marketplace_plugin_runtime = MarketplacePluginRuntime(marketplace)
    Parent = previous.make_handler(app, telegram, settings, model_catalog)

    class Handler(Parent):
        def do_GET(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/chatgpt-shell.js":
                try:
                    return self._send_asset("chatgpt-shell-stable.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "chatgpt-shell-stable.js не найден"})

            if parsed.path == "/marketplace-panel.js":
                try:
                    return self._send_asset("marketplace-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "marketplace-panel.js не найден"})

            if parsed.path in {"/v1/marketplace", "/v1/marketplace/status"}:
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    return self._json(HTTPStatus.OK, marketplace.status())
                except Exception as exc:
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
            if parsed.path.startswith("/v1/marketplace/"):
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    if parsed.path == "/v1/marketplace/refresh":
                        return self._json(HTTPStatus.OK, marketplace.refresh())
                    if parsed.path == "/v1/marketplace/source/add":
                        sources = marketplace.add_source(str(body.get("url") or ""), str(body.get("name") or ""))
                        return self._json(HTTPStatus.OK, {"ok": True, "sources": sources})
                    if parsed.path == "/v1/marketplace/source/remove":
                        sources = marketplace.remove_source(str(body.get("url") or ""))
                        return self._json(HTTPStatus.OK, {"ok": True, "sources": sources})
                    if parsed.path == "/v1/marketplace/install":
                        return self._json(HTTPStatus.OK, marketplace.install(str(body.get("id") or "")))
                    if parsed.path == "/v1/marketplace/toggle":
                        return self._json(
                            HTTPStatus.OK,
                            marketplace.set_enabled(str(body.get("id") or ""), bool(body.get("enabled"))),
                        )
                    if parsed.path == "/v1/marketplace/remove/request":
                        return self._json(HTTPStatus.OK, marketplace.request_remove(str(body.get("id") or "")))
                    if parsed.path == "/v1/marketplace/remove/confirm":
                        return self._json(
                            HTTPStatus.OK,
                            marketplace.confirm_remove(str(body.get("token") or ""), bool(body.get("allow"))),
                        )
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Неизвестная операция Marketplace"})
                except (ValueError, KeyError, FileNotFoundError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("marketplace_http_failed", str(exc), path=parsed.path)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            return super().do_POST()

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    settings = previous.base.Settings.from_env()
    HuggingFaceSettings(settings.brain_dir).apply()
    model_catalog = apply_saved_model_to_settings(settings)
    app = previous.base.LeshyApp(settings)
    app.accelerator_runtime = CompatibleRuntimeManager(
        settings.brain_dir,
        model_name_provider=lambda: app.model_path.name,
    )
    app.llm = app._build_llm()
    _apply_low_memory_guard(app, settings)
    configure_app_model_verifier(app, model_catalog)
    install_streaming_backends(app.llm)
    install_os_control(app)
    app.marketplace = MarketplaceManager(settings.brain_dir)
    app.marketplace_plugin_runtime = MarketplacePluginRuntime(app.marketplace)
    install_marketplace_chat_bridge()

    telegram = FastTelegramBridge(app, settings.brain_dir)
    app.telegram = telegram
    app.start_background()
    threading.Thread(target=previous._warm_local_llm, args=(app,), name="leshy-llm-warmup", daemon=True).start()
    threading.Thread(
        target=previous._delayed_recovery,
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
