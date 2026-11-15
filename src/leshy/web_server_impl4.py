from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl3 as previous
from .diagnostics import problem
from .voice_tts import LocalVoiceTTS


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def _runtime_html(settings) -> bytes:
    html = previous._runtime_html(settings).decode("utf-8")
    marker = '<script src="/voice-settings-panel.js"></script>'
    if marker not in html:
        html = html.replace("</body>", marker + "\n</body>")
    return html.encode("utf-8")


def make_handler(app, telegram, settings, model_catalog):
    Parent = _PREVIOUS_MAKE_HANDLER(app, telegram, settings, model_catalog)
    voice_settings = LocalVoiceTTS(settings.brain_dir)

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
            if parsed.path == "/voice-settings-panel.js":
                try:
                    return self._send_asset("voice-settings-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "voice-settings-panel.js не найден"})
            if parsed.path == "/v1/voice/settings":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    return self._json(HTTPStatus.OK, voice_settings.status())
                except Exception as exc:
                    problem("voice_settings_status_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
            if parsed.path in {"/v1/voice/settings", "/v1/voice/install"}:
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    if parsed.path == "/v1/voice/settings":
                        return self._json(
                            HTTPStatus.OK,
                            voice_settings.configure(
                                selected_gender=str(body.get("selected_gender") or "male"),
                                engine=str(body.get("engine") or "auto"),
                                voice_id=str(body.get("voice_id") or ""),
                                profile=str(body.get("profile") or "natural"),
                                rate=body.get("rate", 1.0),
                            ),
                        )
                    selected_gender = str(body.get("selected_gender") or "male")
                    engine = str(body.get("engine") or "auto")
                    voice_id = str(body.get("voice_id") or "")
                    voice_settings.configure(selected_gender=selected_gender, engine=engine, voice_id=voice_id)
                    return self._json(HTTPStatus.OK, voice_settings.install(engine, voice_id=voice_id))
                except (ValueError, RuntimeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc), "voice": voice_settings.status()})
                except Exception as exc:
                    problem("voice_settings_operation_failed", str(exc), path=parsed.path)
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return super().do_POST()

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    # impl3 содержит проверенную последовательность soft-start/warmup/recovery.
    # Подменяем только фабрику HTTP handler, не меняя runtime и LLM-настройки.
    original = previous.make_handler
    previous.make_handler = make_handler
    try:
        previous.serve(host=host, port=port)
    finally:
        previous.make_handler = original


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
