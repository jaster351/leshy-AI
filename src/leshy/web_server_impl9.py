from __future__ import annotations

from http import HTTPStatus
from urllib import parse

from . import web_server_impl8 as previous
from .aquarium_simulation import AquariumSimulation
from .module_settings import module_enabled
from .diagnostics import problem


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def _runtime_html(settings) -> bytes:
    """Добавить только визуальный слой темы v2 поверх уже настроенного Web UI."""
    html_text = previous._runtime_html(settings).decode("utf-8")
    marker = '<script src="/forest-theme-v2.js"></script>'
    if marker not in html_text:
        html_text = html_text.replace("</body>", marker + "\n</body>")
    aquarium_script = '<script src="/aquarium-panel.js"></script>'
    if aquarium_script not in html_text:
        html_text = html_text.replace("</body>", aquarium_script + "\n</body>")
    return html_text.encode("utf-8")


def _aquarium(app) -> AquariumSimulation:
    value = getattr(app, "aquarium_simulation", None)
    if not isinstance(value, AquariumSimulation):
        value = AquariumSimulation(app.settings.brain_dir, enabled_check=lambda: module_enabled(app.storage, 'aquarium'))
        setattr(app, "aquarium_simulation", value)
    return value


def make_handler(app, telegram, settings, model_catalog):
    aquarium = _aquarium(app)
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
            if parsed.path == "/aquarium-panel.js":
                try:
                    return self._send_asset("aquarium-panel.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "aquarium-panel.js не найден"})
            if parsed.path == "/v1/aquarium/state":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                if not module_enabled(app.storage, "aquarium"):
                    return self._json(HTTPStatus.LOCKED, {"error": "Аквариум выключен в настройках", "enabled": False})
                return self._json(HTTPStatus.OK, aquarium.snapshot())
            if parsed.path == "/v1/aquarium/species":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                if not module_enabled(app.storage, "aquarium"):
                    return self._json(HTTPStatus.LOCKED, {"error": "Аквариум выключен в настройках", "enabled": False})
                args = parse.parse_qs(parsed.query)
                try:
                    profile = aquarium.species.get(self._arg(args, "scientific_name", ""))
                    return self._json(HTTPStatus.OK, {"profile": {f: getattr(profile, f) for f in profile.__dataclass_fields__}})
                except (ValueError, LookupError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
            if parsed.path.startswith("/v1/aquarium/"):
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                if not module_enabled(app.storage, "aquarium"):
                    return self._json(HTTPStatus.LOCKED, {"error": "Аквариум выключен в настройках", "enabled": False})
                try:
                    body = self._body()
                    if parsed.path == "/v1/aquarium/reset":
                        aquarium.reset(str(body.get("mode") or "freshwater"), float(body.get("volume_l") or 120))
                        return self._json(HTTPStatus.OK, aquarium.snapshot())
                    if parsed.path == "/v1/aquarium/add":
                        profile = aquarium.add_species(str(body.get("scientific_name") or ""), int(body.get("count") or 1))
                        return self._json(HTTPStatus.OK, {"profile": {f: getattr(profile, f) for f in profile.__dataclass_fields__}, "state": aquarium.snapshot()})
                    if parsed.path == "/v1/aquarium/tick":
                        return self._json(HTTPStatus.OK, aquarium.tick(float(body.get("hours") or 1)))
                    if parsed.path == "/v1/aquarium/action":
                        action = body.get("action") if isinstance(body.get("action"), dict) else body
                        message = aquarium.apply_action(dict(action), actor="user")
                        return self._json(HTTPStatus.OK, {"message": message, "state": aquarium.snapshot()})
                    if parsed.path == "/v1/aquarium/leshy-step":
                        return self._json(HTTPStatus.OK, aquarium.leshy_step(app.llm))
                except PermissionError as exc:
                    return self._json(HTTPStatus.LOCKED, {"error": str(exc), "enabled": False})
                except (ValueError, LookupError, TypeError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("aquarium_http_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return super().do_POST()

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
