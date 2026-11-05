from __future__ import annotations

import html
from http import HTTPStatus
from urllib import parse

from . import web_server_impl6 as previous
from . import web_server_impl5 as shell
from .activity_visibility_runtime import install_activity_visibility
from .advanced_modules_runtime import install_advanced_modules, sync_advanced_module_state
from .app import LeshyApp
from .chat_idempotency import install_global_chat_idempotency
from .diagnostics import event, problem
from .freshness_cache_runtime import install_freshness_cache_runtime
from .freshness_memory_runtime import install_freshness_memory_runtime
from .honest_uncertainty_runtime import install_honest_uncertainty
from .identity_runtime import install_creator_identity_routing
from .interactive_search_budget import install_interactive_search_budget
from .learning_command_runtime import install_explicit_learning_routing
from .lenta_preferences_runtime import install_lenta_preferences_runtime
from .lenta_runtime import install_lenta_routing
from .module_settings import list_module_states, module_enabled, set_module_enabled
from .cluster_orchestrator import cluster_public_summary
from .network_blocking_runtime import install_network_blocking_routing
from .parcel_tracking_runtime import install_parcel_tracking_routing, sync_parcel_runtime_state
from .reliable_web_runtime import install_reliable_web_runtime
from .runtime_latency_fix import install_runtime_latency_fixes
from .session_admin import SessionAdminService
from .superagent_runtime import install_superagent_routing, sync_superagent_runtime_state
from .superagent_scheduler import install_superagent_background_scheduler
from .telegram_exact_once import install_telegram_exact_once_finalization
from .telegram_fast import FastTelegramBridge


_ORIGINAL_SHELL_MAKE_HANDLER = shell.make_handler


def _restore_reference_chat_timing() -> None:
    """Вернуть Telegram-поведение эталона 1.10.35 без потери новых функций."""
    FastTelegramBridge.edit_interval_seconds = 0.55

    def no_foreground_pre_wait(self) -> None:
        return None

    FastTelegramBridge._wait_for_foreground_slot = no_foreground_pre_wait


def _install_runtime_contracts(app=None) -> None:
    """Ставить idempotency последним, поверх всех специальных chat routing."""
    install_explicit_learning_routing(LeshyApp)
    install_reliable_web_runtime(LeshyApp)
    install_freshness_memory_runtime(app)
    install_freshness_cache_runtime()
    install_runtime_latency_fixes(app)
    install_interactive_search_budget(LeshyApp)
    install_creator_identity_routing(LeshyApp)
    # Специализированные модули идут до общей LLM и могут быть выключены из Settings.
    install_parcel_tracking_routing(LeshyApp, app)
    install_network_blocking_routing(LeshyApp, app)
    install_superagent_background_scheduler()
    install_superagent_routing(LeshyApp, app)
    # Browser Platform уже ставится внешним impl8. «Лента» должна оборачивать его,
    # чтобы продолжения «да/нет/измени» не перехватывались общим Browser Agent.
    install_lenta_routing(LeshyApp, app)
    install_lenta_preferences_runtime()
    # Интеллектуальная память, GraphRAG, команда подагентов и распределённый кластер
    # подключаются как единый вычислительный слой, но каждый модуль выключается отдельно.
    install_advanced_modules(LeshyApp, app)
    install_activity_visibility(app)
    # Последний смысловой предохранитель до idempotency: пустой ответ никогда не
    # должен превращаться в бесконечное ожидание или молчание пользователя.
    install_honest_uncertainty(LeshyApp)
    _restore_reference_chat_timing()
    install_telegram_exact_once_finalization()
    install_global_chat_idempotency()


def _runtime_html(settings) -> bytes:
    """Эталонный chat transport + изолированные UI-модули вокруг него."""
    reference_html = shell._runtime_html(settings)
    html_text = reference_html.decode("utf-8")
    # Не модифицируем эталонный chat transport, если внешняя оболочка
    # не содержит полноценной боковой панели: скрипты модулей там не нужны.
    # Это также сохраняет точный результат HTML для минимальных/встраиваемых UI.
    if 'class="sidebar"' not in html_text and 'id="pane-chat"' not in html_text:
        return reference_html
    for marker in (
        '<script src="/sidebar-session-actions.js"></script>',
        '<script src="/chat-layout-status.js"></script>',
        '<script src="/module-settings-panel.js"></script>',
    ):
        if marker not in html_text:
            html_text = html_text.replace("</body>", marker + "\n</body>")
    return html_text.encode("utf-8")


def _shared_chat_html(payload: dict) -> bytes:
    session = payload.get("session") or {}
    title = html.escape(str(session.get("title") or "Диалог с Лешим"))
    blocks: list[str] = []
    for message in payload.get("messages") or []:
        role = str(message.get("role") or "assistant")
        who = "Вы" if role == "user" else "Леший"
        body = html.escape(str(message.get("content") or "")).replace("\n", "<br>")
        css = "user" if role == "user" else "assistant"
        blocks.append(
            f'<article class="msg {css}"><div class="who">{who}</div><div>{body}</div></article>'
        )
    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
html,body{{margin:0;background:#212121;color:#ececec;font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:820px;margin:0 auto;padding:34px 18px 70px}}h1{{font-size:20px;margin:0 0 26px}}.muted{{color:#999;font-size:12px;margin-bottom:24px}}
.msg{{margin:0 0 18px;line-height:1.55;word-break:break-word}}.msg.user{{width:fit-content;max-width:78%;margin-left:auto;background:#303030;padding:10px 15px;border-radius:20px}}.msg.assistant{{padding:4px 0 16px}}.who{{font-size:11px;color:#9b9b9b;margin-bottom:6px}}
</style></head><body><main><h1>{title}</h1><div class="muted">Общая ссылка · только чтение</div>{''.join(blocks)}</main></body></html>"""
    return page.encode("utf-8")


def _sync_runtime_after_module_change(app, module_name: str) -> None:
    """Применить переключатель сразу, без перезапуска Лешего."""
    if module_name in {"parcel-tracking", "human-browser"}:
        sync_parcel_runtime_state(app)
    if module_name == "superagent":
        sync_superagent_runtime_state(app)
    sync_advanced_module_state(app, module_name)


def make_handler(app, telegram, settings, model_catalog):
    _install_runtime_contracts(app)
    Parent = _ORIGINAL_SHELL_MAKE_HANDLER(app, telegram, settings, model_catalog)
    session_organizer = SessionAdminService(app.storage)

    class Handler(Parent):
        def _stream_chat(self, body: dict) -> None:
            request_id = str(body.get("request_id") or "").strip()
            owner = str(body.get("owner_id") or "web:local").strip()[:200]
            sid = str(body.get("session_id") or "").strip()[:200]
            event(
                "web_chat_stream_received",
                request_id=request_id,
                owner_id=owner,
                session_id=sid,
            )
            try:
                return super()._stream_chat(body)
            finally:
                event(
                    "web_chat_stream_http_finished",
                    request_id=request_id,
                    owner_id=owner,
                    session_id=sid,
                )

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
            if parsed.path.startswith("/share/"):
                token = parse.unquote(parsed.path[len("/share/"):]).strip()
                try:
                    data = _shared_chat_html(session_organizer.resolve_share(token))
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                except ValueError:
                    self.send_error(HTTPStatus.NOT_FOUND, "Общая ссылка недействительна или отозвана")
                return
            if parsed.path in {
                "/chat-idempotency.js",
                "/chat-stall-watchdog.js",
                "/chat-controller-canonical.js",
                "/chat-followup-finalizer.js",
                "/sidebar-session-actions.js",
                "/chat-layout-status.js",
                "/module-settings-panel.js",
            }:
                name = parsed.path.rsplit("/", 1)[-1]
                try:
                    return self._send_asset(name, "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": f"{name} не найден"})
            if parsed.path == "/v1/cluster/summary":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                enabled = module_enabled(app.storage, "distributed-cluster")
                cluster = getattr(app, "distributed_cluster", None)
                try:
                    status = cluster.status() if enabled and cluster is not None else {}
                    return self._json(
                        HTTPStatus.OK,
                        cluster_public_summary(status, enabled=enabled and cluster is not None),
                    )
                except Exception as exc:
                    problem("cluster_public_summary_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось получить статистику кластера"})
            if parsed.path == "/v1/modules":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                return self._json(HTTPStatus.OK, {"items": list_module_states(app.storage)})
            if parsed.path == "/v1/chat-projects":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                args = parse.parse_qs(parsed.query)
                owner = self._arg(args, "owner_id", "web:local")
                return self._json(HTTPStatus.OK, {"items": session_organizer.projects(owner)})
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/v1/modules/toggle":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    name = str(body.get("name") or "").strip()
                    enabled = body.get("enabled")
                    if not name or not isinstance(enabled, bool):
                        raise ValueError("Нужны name и enabled=true/false")
                    state = set_module_enabled(app.storage, name, enabled)
                    _sync_runtime_after_module_change(app, name)
                    items = list_module_states(app.storage)
                    message = f"Модуль «{state['title']}» {'включён' if enabled else 'выключен'}."
                    if enabled and not state.get("enabled"):
                        disabled = [dep.get("name") for dep in state.get("dependencies", []) if not dep.get("enabled")]
                        if disabled:
                            message += " Для работы нужно включить зависимость: " + ", ".join(disabled) + "."
                    return self._json(HTTPStatus.OK, {"state": state, "items": items, "message": message})
                except (KeyError, ValueError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("module_setting_operation_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path in {
                "/v1/chat-projects/create",
                "/v1/sessions/project",
                "/v1/sessions/share",
                "/v1/sessions/share/revoke",
            }:
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner = str(body.get("owner_id") or "web:local").strip()[:200]
                    if parsed.path == "/v1/chat-projects/create":
                        return self._json(HTTPStatus.OK, session_organizer.create_project(owner, str(body.get("name") or "")))
                    sid = str(body.get("id") or "").strip()[:200]
                    if parsed.path == "/v1/sessions/project":
                        project_id = str(body.get("project_id") or "").strip() or None
                        return self._json(HTTPStatus.OK, session_organizer.move_to_project(owner, sid, project_id))
                    if parsed.path == "/v1/sessions/share":
                        result = session_organizer.create_share(owner, sid)
                        result["path"] = "/share/" + str(result["token"])
                        return self._json(HTTPStatus.OK, result)
                    return self._json(HTTPStatus.OK, session_organizer.revoke_share(owner, sid))
                except (ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("session_sidebar_operation_failed", str(exc), path=parsed.path)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path != "/v1/chat":
                return super().do_POST()
            if not self._authorized():
                return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
            try:
                body = self._body()
                owner = str(body.get("owner_id") or "web:local").strip()[:200]
                sid = str(body.get("session_id") or "").strip()[:200] or None
                if sid:
                    with app.storage.connection() as conn:
                        row = conn.execute(
                            "SELECT owner_id,locked FROM sessions WHERE id=?",
                            (sid,),
                        ).fetchone()
                    if row is not None and str(row["owner_id"]) != owner:
                        raise PermissionError("Диалог принадлежит другому пользователю")
                    if row is not None and bool(row["locked"]):
                        raise PermissionError("Этот чат заблокирован. Разблокируйте его в меню чата.")
                result = app.chat(
                    str(body.get("message") or ""),
                    owner_id=owner,
                    session_id=sid,
                    request_id=str(body.get("request_id") or "").strip() or None,
                    channel="web-http",
                )
                return self._json(HTTPStatus.OK, result)
            except (ValueError, PermissionError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:
                problem("chat_http_failed", str(exc))
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    original = shell.make_handler
    shell.make_handler = make_handler
    try:
        previous.serve(host=host, port=port)
    finally:
        shell.make_handler = original


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
