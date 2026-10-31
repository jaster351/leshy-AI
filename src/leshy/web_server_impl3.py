from __future__ import annotations

import json
import os
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib import parse

from . import web_server_impl2 as previous
from .diagnostics import event, problem
from .github_plugin import GitHubPluginService
from .hf_settings import HuggingFaceSettings
from .marketplace import MarketplaceManager
from .marketplace_chat import install_marketplace_chat_bridge
from .marketplace_discovery import discover as discover_marketplace, install as install_marketplace_item
from .marketplace_plugin_runtime import MarketplacePluginRuntime
from .memory_policy import memory_status
from .model_catalog import apply_saved_model_to_settings, configure_app_model_verifier
from .os_control_approval import install_os_control
from .runtime_compat import CompatibleRuntimeManager
from .streaming_backends import install_streaming_backends
from .telegram_fast import FastTelegramBridge


_SOFT_WARMUP_DELAY_SECONDS = 2.0
_BACKGROUND_AFTER_WARMUP_SECONDS = 120.0
_AUTO_TITLE_QUIET_SECONDS = 180.0
_TELEGRAM_MINIMUM_DELAY_SECONDS = 30.0


_GITHUB_PLUGIN_MANIFEST = {
    "schema_version": 1,
    "id": "github",
    "name": "GitHub",
    "type": "plugin",
    "version": "1.0.0",
    "description": "Официальный плагин Лешего для чтения разрешённых GitHub-репозиториев: исходный код, commits, Issues и Pull Requests.",
    "developer": {"name": "Leshy AI", "url": "https://github.com/jaster351/leshy-AI"},
    "homepage": "https://github.com/jaster351/leshy-AI",
    "license": "GPL-3.0",
    "permissions": ["github:read", "network:https", "secrets:github-token"],
    "plugin": {
        "official_builtin": "github",
        "tools": [
            {
                "name": "github",
                "builtin": "github",
                "description": "Читает исходный код, commits, Issues и Pull Requests из разрешённых GitHub-репозиториев.",
                "triggers": [
                    "github", "гитхаб", "репозитор", "исходный код", "исходники", "commit", "коммит",
                    "issue", "pull request", "telegram.py", "voice", "marketplace", "web ui"
                ],
            }
        ],
    },
}


def _ensure_official_github_plugin(marketplace: MarketplaceManager) -> None:
    """Установить/обновить только официальный встроенный GitHub manifest.

    Если каталог `plugins/github` уже занят сторонним пакетом, ничего не
    перезаписываем. Пользовательские секреты и настройки здесь не хранятся.
    """
    try:
        target = marketplace.root / "github"
        manifest_path = target / "leshy-plugin.json"
        if manifest_path.is_file():
            try:
                current = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                current = None
            plugin = current.get("plugin") if isinstance(current, dict) and isinstance(current.get("plugin"), dict) else {}
            if str(plugin.get("official_builtin") or "") != "github":
                problem("github_plugin_install_skipped", "plugins/github уже занят другим расширением")
                return
        target.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(_GITHUB_PLUGIN_MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, manifest_path)
        event("github_plugin_manifest_ready", version=_GITHUB_PLUGIN_MANIFEST["version"])
    except Exception as exc:
        problem("github_plugin_manifest_failed", str(exc))


def _apply_low_memory_guard(app, settings) -> None:
    """На небольшом объёме RAM не держать лишнюю CPU-копию и уменьшить KV-контекст."""
    try:
        status = memory_status(settings.brain_dir)
        total = int((status.get("system") or {}).get("ram_total_bytes") or 0)
        states = list(getattr(app.llm, "_states", []) or [])
        if not total or total > 20 * 1024**3 or not states:
            return

        has_cpu_server = any(getattr(state, "name", "") == "cpu-llama-server" for state in states)
        kept = [
            state for state in states
            if not (has_cpu_server and getattr(state, "name", "") == "cpu-native")
        ]
        if len(kept) != len(states):
            app.llm._states = kept
            app.llm.model = "adaptive:" + ",".join(str(getattr(state, "name", "")) for state in kept)
            event(
                "low_memory_cpu_native_disabled",
                ram_total_bytes=total,
                remaining_backends=[state.name for state in kept],
            )

        capped: list[dict[str, int | str]] = []
        for state in kept:
            provider = getattr(state, "provider", None)
            if provider is None or not hasattr(provider, "n_ctx"):
                continue
            name = str(getattr(state, "name", ""))
            if name not in {"gpu-vulkan", "cpu-llama-server"}:
                continue
            before = int(getattr(provider, "n_ctx", 4096) or 4096)
            after = min(before, 4096)
            if after != before:
                provider.n_ctx = after
                capped.append({"backend": name, "before": before, "after": after})
        if capped:
            event("low_memory_context_capped", ram_total_bytes=total, backends=capped)
    except Exception as exc:
        problem("low_memory_guard_failed", str(exc))


def _install_startup_title_guard() -> None:
    """Не запускать скрытую LLM-генерацию названий во время тяжёлого старта."""
    try:
        DashboardService = previous.DashboardService
        if getattr(DashboardService, "_leshy_startup_title_guard", False):
            return
        original = DashboardService._schedule_auto_title

        def guarded(self, owner, rows):
            quiet_until = float(getattr(self.app, "startup_quiet_until_monotonic", 0.0) or 0.0)
            if quiet_until and time.monotonic() < quiet_until:
                return
            return original(self, owner, rows)

        DashboardService._schedule_auto_title = guarded
        DashboardService._leshy_startup_title_guard = True
    except Exception as exc:
        problem("startup_title_guard_failed", str(exc))


def _warmup_soft(app, ready: threading.Event, shutdown: threading.Event) -> None:
    if shutdown.wait(_SOFT_WARMUP_DELAY_SECONDS):
        ready.set()
        return
    try:
        previous._warm_local_llm(app)
    finally:
        ready.set()


def _deferred_background(app, ready: threading.Event, shutdown: threading.Event) -> None:
    while not ready.is_set():
        if shutdown.wait(0.5):
            return
    if shutdown.wait(_BACKGROUND_AFTER_WARMUP_SECONDS):
        return
    try:
        app.start_background()
        event(
            "background_deferred_started",
            delay_after_warmup_seconds=_BACKGROUND_AFTER_WARMUP_SECONDS,
        )
    except Exception as exc:
        problem("background_deferred_start_failed", str(exc))


def _delayed_recovery_after_ready(
    app,
    telegram,
    ready: threading.Event,
    shutdown: threading.Event,
    started_monotonic: float,
) -> None:
    minimum_remaining = _TELEGRAM_MINIMUM_DELAY_SECONDS - (time.monotonic() - started_monotonic)
    if minimum_remaining > 0 and shutdown.wait(minimum_remaining):
        return
    while not ready.is_set():
        if shutdown.wait(0.5):
            return
    try:
        telegram.start()
    except Exception as exc:
        problem("telegram_delayed_start_failed", str(exc))
    try:
        result = previous.base.recover_unanswered(app)
        event(
            "startup_recovery_cycle_finished",
            delay_seconds=round(time.monotonic() - started_monotonic, 1),
            **result,
        )
    except Exception as exc:
        problem(
            "startup_recovery_cycle_failed",
            str(exc),
            delay_seconds=round(time.monotonic() - started_monotonic, 1),
        )


def _runtime_html(settings) -> bytes:
    """Единая оболочка Web UI. ChatGPT-подобную компоновку не меняем без явной просьбы пользователя."""
    html = previous.base._inject_runtime_ui(previous.base._web_index(settings).read_text(encoding="utf-8"))
    markers = (
        '<script src="/chatgpt-shell.js"></script>',
        '<script src="/right-menu-fix.js"></script>',
        '<script src="/os-approval-ui.js"></script>',
        '<script src="/hf-settings-panel.js"></script>',
        '<script src="/marketplace-panel.js"></script>',
        '<script src="/github-plugin-panel.js"></script>',
        '<script src="/thinking-indicator.js"></script>',
        '<script src="/stream-activity-bridge.js"></script>',
        '<script src="/voice-live.js"></script>',
        '<script src="/chat-multitask.js"></script>',
    )
    for marker in markers:
        if marker not in html:
            html = html.replace("</body>", marker + "\n</body>")
    return html.encode("utf-8")


def make_handler(app, telegram, settings, model_catalog):
    marketplace = getattr(app, "marketplace", None)
    if marketplace is None:
        marketplace = MarketplaceManager(settings.brain_dir)
        app.marketplace = marketplace
        _ensure_official_github_plugin(marketplace)
    github_plugin = getattr(app, "github_plugin", None)
    if github_plugin is None:
        github_plugin = GitHubPluginService(settings.brain_dir)
        app.github_plugin = github_plugin
    if getattr(app, "marketplace_plugin_runtime", None) is None:
        app.marketplace_plugin_runtime = MarketplacePluginRuntime(marketplace, builtin_services={"github": github_plugin})
    Parent = previous.make_handler(app, telegram, settings, model_catalog)

    class Handler(Parent):
        def do_GET(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/":
                data = _runtime_html(settings)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return

            if parsed.path == "/chatgpt-shell.js":
                try:
                    return self._send_asset("chatgpt-shell-stable.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "chatgpt-shell-stable.js не найден"})

            if parsed.path in {"/marketplace-panel.js", "/github-plugin-panel.js", "/chat-multitask.js"}:
                name = parsed.path.rsplit("/", 1)[-1]
                try:
                    return self._send_asset(name, "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": f"{name} не найден"})

            if parsed.path == "/v1/marketplace/github/status":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    return self._json(HTTPStatus.OK, github_plugin.status())
                except Exception as exc:
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

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
                    if parsed.path == "/v1/marketplace/github/config":
                        repositories = body.get("repositories")
                        if isinstance(repositories, str):
                            repositories = [x.strip() for x in repositories.replace(",", "\n").splitlines() if x.strip()]
                        if repositories is not None and not isinstance(repositories, list):
                            raise ValueError("repositories должен быть списком")
                        return self._json(
                            HTTPStatus.OK,
                            github_plugin.configure(
                                repositories=repositories,
                                default_repository=str(body.get("default_repository") or ""),
                                token=str(body.get("token") or ""),
                                clear_token=bool(body.get("clear_token")),
                                write_enabled=bool(body.get("write_enabled")) if "write_enabled" in body else None,
                            ),
                        )
                    if parsed.path == "/v1/marketplace/discover":
                        return self._json(HTTPStatus.OK, discover_marketplace(marketplace, str(body.get("query") or "")))
                    if parsed.path == "/v1/marketplace/refresh":
                        return self._json(HTTPStatus.OK, marketplace.refresh())
                    if parsed.path == "/v1/marketplace/source/add":
                        sources = marketplace.add_source(str(body.get("url") or ""), str(body.get("name") or ""))
                        return self._json(HTTPStatus.OK, {"ok": True, "sources": sources})
                    if parsed.path == "/v1/marketplace/source/remove":
                        sources = marketplace.remove_source(str(body.get("url") or ""))
                        return self._json(HTTPStatus.OK, {"ok": True, "sources": sources})
                    if parsed.path == "/v1/marketplace/install":
                        return self._json(HTTPStatus.OK, install_marketplace_item(marketplace, str(body.get("id") or "")))
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
    started_monotonic = time.monotonic()
    settings = previous.base.Settings.from_env()
    HuggingFaceSettings(settings.brain_dir).apply()
    model_catalog = apply_saved_model_to_settings(settings)
    app = previous.base.LeshyApp(settings)

    old_llm = app.llm
    app.accelerator_runtime = CompatibleRuntimeManager(
        settings.brain_dir,
        model_name_provider=lambda: app.model_path.name,
    )
    app.llm = app._build_llm()
    old_close = getattr(old_llm, "close", None)
    if callable(old_close):
        try:
            old_close()
        except Exception:
            pass

    _apply_low_memory_guard(app, settings)
    app.startup_quiet_until_monotonic = started_monotonic + _AUTO_TITLE_QUIET_SECONDS
    _install_startup_title_guard()
    configure_app_model_verifier(app, model_catalog)
    install_streaming_backends(app.llm)
    install_os_control(app)
    app.marketplace = MarketplaceManager(settings.brain_dir)
    _ensure_official_github_plugin(app.marketplace)
    app.github_plugin = GitHubPluginService(settings.brain_dir)
    app.marketplace_plugin_runtime = MarketplacePluginRuntime(
        app.marketplace,
        builtin_services={"github": app.github_plugin},
    )
    install_marketplace_chat_bridge()

    telegram = FastTelegramBridge(app, settings.brain_dir)
    app.telegram = telegram

    warmup_ready = threading.Event()
    shutdown = threading.Event()

    bind_host = host or os.getenv("LESHY_HOST", "0.0.0.0")
    bind_port = int(port or os.getenv("LESHY_PORT", "8765"))
    server = ThreadingHTTPServer((bind_host, bind_port), make_handler(app, telegram, settings, model_catalog))

    threading.Thread(
        target=_warmup_soft,
        args=(app, warmup_ready, shutdown),
        name="leshy-llm-warmup",
        daemon=True,
    ).start()
    threading.Thread(
        target=_deferred_background,
        args=(app, warmup_ready, shutdown),
        name="leshy-background-deferred",
        daemon=True,
    ).start()
    threading.Thread(
        target=_delayed_recovery_after_ready,
        args=(app, telegram, warmup_ready, shutdown, started_monotonic),
        name="leshy-startup-recovery",
        daemon=True,
    ).start()

    event(
        "soft_start_enabled",
        warmup_delay_seconds=_SOFT_WARMUP_DELAY_SECONDS,
        background_delay_after_warmup_seconds=_BACKGROUND_AFTER_WARMUP_SECONDS,
        auto_title_quiet_seconds=_AUTO_TITLE_QUIET_SECONDS,
        telegram_minimum_delay_seconds=_TELEGRAM_MINIMUM_DELAY_SECONDS,
    )
    print(f"Леший {app.status()['version']} запущен: http://{bind_host}:{bind_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown.set()
        telegram.stop()
        app.stop_background()
        server.server_close()


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
