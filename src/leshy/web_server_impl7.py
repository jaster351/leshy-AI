from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl6 as previous
from . import web_server_impl5 as shell
from .app import LeshyApp
from .chat_idempotency import install_global_chat_idempotency
from .diagnostics import problem
from .freshness_memory_runtime import install_freshness_memory_runtime
from .identity_runtime import install_creator_identity_routing
from .learning_command_runtime import install_explicit_learning_routing
from .reliable_web_runtime import install_reliable_web_runtime
from .runtime_latency_fix import install_runtime_latency_fixes
from .telegram_exact_once import install_telegram_exact_once_finalization


_ORIGINAL_SHELL_MAKE_HANDLER = shell.make_handler


def _install_runtime_contracts(app=None) -> None:
    """Ставить idempotency последним, поверх всех специальных chat routing."""
    install_explicit_learning_routing(LeshyApp)
    install_reliable_web_runtime(LeshyApp)
    # Freshness-layer должен быть внутри latency-layer, чтобы stale fallback
    # попал в общую компактизацию prompt и не переполнил 4096-token backend.
    install_freshness_memory_runtime(app)
    install_runtime_latency_fixes(app)
    install_creator_identity_routing(LeshyApp)
    install_telegram_exact_once_finalization()
    install_global_chat_idempotency()


def _runtime_html(settings) -> bytes:
    html = shell._runtime_html(settings).decode("utf-8")
    for marker in (
        '<script src="/chat-idempotency.js"></script>',
        '<script src="/chat-stall-watchdog.js"></script>',
    ):
        if marker not in html:
            html = html.replace("</body>", marker + "\n</body>")
    return html.encode("utf-8")


def make_handler(app, telegram, settings, model_catalog):
    _install_runtime_contracts(app)
    Parent = _ORIGINAL_SHELL_MAKE_HANDLER(app, telegram, settings, model_catalog)

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
            if parsed.path in {"/chat-idempotency.js", "/chat-stall-watchdog.js"}:
                name = parsed.path.rsplit("/", 1)[-1]
                try:
                    return self._send_asset(name, "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": f"{name} не найден"})
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
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
    # impl6 устанавливает verified runtime policies до создания приложения.
    # Подменяем фабрику impl5, потому что именно она каскадно передаётся вниз до
    # ThreadingHTTPServer в текущей совместимой цепочке impl3→impl4→impl5→impl6.
    original = shell.make_handler
    shell.make_handler = make_handler
    try:
        previous.serve(host=host, port=port)
    finally:
        shell.make_handler = original


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
