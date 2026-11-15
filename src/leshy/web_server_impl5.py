from __future__ import annotations

from hashlib import sha256
from http import HTTPStatus
from urllib import parse
import uuid

from . import web_server_impl4 as previous
from .diagnostics import problem
from .storage import utcnow


_PREVIOUS_MAKE_HANDLER = previous.make_handler


def _runtime_html(settings) -> bytes:
    html = previous._runtime_html(settings).decode("utf-8")
    marker = '<script src="/message-actions.js"></script>'
    if marker not in html:
        html = html.replace("</body>", marker + "\n</body>")
    return html.encode("utf-8")


def _clean_branch_messages(raw) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("messages должен быть списком")
    if len(raw) > 120:
        raise ValueError("В ветке слишком много сообщений")
    cleaned: list[dict[str, str]] = []
    total_chars = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 32000:
            raise ValueError("Одно из сообщений ветки слишком длинное")
        total_chars += len(content)
        if total_chars > 240000:
            raise ValueError("Контекст ветки слишком большой")
        cleaned.append({"role": role, "content": content})
    return cleaned


def make_handler(app, telegram, settings, model_catalog):
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
            if parsed.path == "/message-actions.js":
                try:
                    return self._send_asset("message-actions.js", "application/javascript; charset=utf-8")
                except FileNotFoundError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "message-actions.js не найден"})
            return super().do_GET()

        def do_POST(self):
            parsed = parse.urlparse(self.path)
            if parsed.path == "/v1/chat/branch":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner_id = str(body.get("owner_id") or "web:local").strip()[:200]
                    source_session_id = str(body.get("source_session_id") or "").strip()[:200]
                    title = " ".join(str(body.get("title") or "Ветка разговора").split()).strip()[:80] or "Ветка разговора"
                    messages = _clean_branch_messages(body.get("messages"))
                    now = utcnow()
                    session_id = str(uuid.uuid4())
                    with app.storage.connection() as conn:
                        if source_session_id:
                            row = conn.execute(
                                "SELECT owner_id FROM sessions WHERE id=?",
                                (source_session_id,),
                            ).fetchone()
                            if row is not None and str(row["owner_id"]) != owner_id:
                                raise PermissionError("Исходный диалог принадлежит другому пользователю")
                        conn.execute(
                            "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                            (session_id, owner_id, title, "branch", now, now),
                        )
                        for item in messages:
                            conn.execute(
                                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                                (session_id, item["role"], item["content"], now),
                            )
                    app.storage.audit(
                        "session.branched",
                        {
                            "session_id": session_id,
                            "source_session_id": source_session_id,
                            "owner_id": owner_id,
                            "message_count": len(messages),
                        },
                    )
                    return self._json(
                        HTTPStatus.OK,
                        {"session_id": session_id, "message_count": len(messages), "title": title},
                    )
                except (ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("chat_branch_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            if parsed.path == "/v1/chat/feedback":
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Требуется API token"})
                try:
                    body = self._body()
                    owner_id = str(body.get("owner_id") or "web:local").strip()[:200]
                    session_id = str(body.get("session_id") or "").strip()[:200]
                    kind = str(body.get("kind") or "").strip().lower()
                    if kind not in {"up", "down", "clear"}:
                        raise ValueError("Некорректная оценка ответа")
                    if session_id:
                        with app.storage.connection() as conn:
                            row = conn.execute("SELECT owner_id FROM sessions WHERE id=?", (session_id,)).fetchone()
                            if row is not None and str(row["owner_id"]) != owner_id:
                                raise PermissionError("Диалог принадлежит другому пользователю")
                    content = str(body.get("content") or "")[:1000]
                    app.storage.audit(
                        "chat.feedback",
                        {
                            "owner_id": owner_id,
                            "session_id": session_id,
                            "kind": kind,
                            "role": str(body.get("role") or "assistant")[:20],
                            "content_sha256": sha256(content.encode("utf-8")).hexdigest() if content else "",
                        },
                    )
                    return self._json(HTTPStatus.OK, {"ok": True, "kind": kind})
                except (ValueError, PermissionError) as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    problem("chat_feedback_failed", str(exc))
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

            return super().do_POST()

    return Handler


def serve(host: str | None = None, port: int | None = None) -> None:
    # impl4 уже сохраняет проверенный soft-start, LLM scheduler и голосовые настройки.
    # Здесь добавляются только HTTP/UI действия сообщений, без изменения inference.
    original = previous.make_handler
    previous.make_handler = make_handler
    try:
        previous.serve(host=host, port=port)
    finally:
        previous.make_handler = original


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
