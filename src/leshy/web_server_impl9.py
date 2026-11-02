from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl8 as previous


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def _runtime_html(settings) -> bytes:
    """Добавить только визуальный слой темы v2 поверх уже настроенного Web UI."""
    html_text = previous._runtime_html(settings).decode("utf-8")
    marker = '<script src="/forest-theme-v2.js"></script>'
    if marker not in html_text:
        html_text = html_text.replace("</body>", marker + "\n</body>")
    return html_text.encode("utf-8")


def make_handler(app, telegram, settings, model_catalog):
    # Разговорные runtime-контракты, включая «Ленту», ставит impl7 до глобальной
    # idempotency. Здесь намеренно меняется только внешний визуальный слой Web UI.
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)

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
            if parsed.path == "/forest-theme-v2.js":
                try:
                    return self._send_asset("forest-theme-v2.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "forest-theme-v2.js не найден"})
            return super().do_GET()

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
