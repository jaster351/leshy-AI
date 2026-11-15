"""Route only explicit owner commands to Parser for Plex.

Works in both HTTP chat and streaming chat; other conversations are unchanged.
"""
from __future__ import annotations

from .torrent_parser_bridge import TorrentParserBridge, parse_request, parse_premiere_request
from .storage import utcnow
from .diagnostics import event

def is_explicit_parser_request(message: str) -> bool:
    """Only the actual user message may trigger a NAS operation."""
    return parse_request(message) is not None or parse_premiere_request(message) is not None


def authorized_owner_for_parser(app, bridge, owner_id: str) -> str:
    """Resolve only an explicitly linked Telegram identity, never by nickname.

    A bot administrator is not automatically the owner of another user's
    Web-configured NAS. A confirmed Owner Identity link is required.
    """
    value = str(owner_id or "").strip()
    if bridge.owner_allowed(value):
        return value
    if not value.startswith("telegram:"):
        return value
    service = getattr(app, "owner_identity", None)
    resolve = getattr(service, "resolve_telegram", None)
    telegram_id = value.partition(":")[2].strip()
    if not callable(resolve) or not telegram_id:
        return value
    try:
        resolved = str(resolve(telegram_id) or "").strip()
    except Exception:
        return value
    return resolved if resolved and bridge.owner_allowed(resolved) else value


def execute_parser_chat(app, message: str, *, owner_id: str, session_id: str | None):
    """One deterministic request path for both Web and Telegram.

    Caller must have verified its user identity and anti-duplicate claim.
    Never retries a NAS command when delivery outcome is uncertain.
    """
    if not is_explicit_parser_request(message):
        raise ValueError("Это не команда Parser for Plex")
    bridge = TorrentParserBridge(app.settings.brain_dir)
    parser_owner = authorized_owner_for_parser(app, bridge, owner_id)
    event("parser_command_routed", configured=bool(bridge._read().get("configured")),
          owner_match=bool(bridge.owner_allowed(parser_owner)), channel=(
              "telegram" if str(owner_id).startswith("telegram:") else "web-or-linked"
          ))
    answer = str(bridge.execute_chat(parser_owner, message) or "Parser for Plex не вернул ответ.")
    sid = app._ensure_session(owner_id, session_id)
    created = utcnow()
    with app.storage.connection() as db:
        db.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
            (sid, "user", str(message), created),
        )
        db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (created, sid))
    completed = app._save_assistant(sid, answer)
    return {"session_id": sid, "answer": answer,
            "created_at": completed, "sources": []}


def stream_parser_chat(manager, message: str, *, owner_id: str,
                       session_id: str | None, request_id: str | None):
    """Keep stop support; never hide a result of a possibly executed NAS job."""
    from .realtime_chat import StreamEvent
    rid, cancel = manager._register(request_id)
    try:
        if cancel.is_set():
            yield StreamEvent("done", {"request_id": rid, "session_id": session_id or "",
                                       "answer": "", "stopped": True, "sources": []})
            return
        result = execute_parser_chat(manager.app, message, owner_id=owner_id, session_id=session_id)
        answer = str(result.get("answer") or "")
        yield StreamEvent("start", {"request_id": rid, "session_id": result["session_id"],
                                    "sources": []})
        if answer:
            yield StreamEvent("delta", {"text": answer})
        yield StreamEvent("done", {"request_id": rid, "session_id": result["session_id"],
                                   "answer": answer, "sources": [],
                                   "created_at": result.get("created_at"), "stopped": False,
                                   "cancel_requested": bool(cancel.is_set()),
                                   "parser_for_plex": True})
    finally:
        manager._unregister(rid)


def install_torrent_parser_chat(app_class, stream_class):
    if not getattr(app_class, "_torrent_bridge_hooked", False):
        original_chat = app_class.chat

        def chat(self, message, *, owner_id="web:local", session_id=None):
            if not is_explicit_parser_request(message):
                return original_chat(self, message, owner_id=owner_id, session_id=session_id)
            return execute_parser_chat(self, message, owner_id=owner_id, session_id=session_id)

        app_class.chat = chat
        app_class._torrent_bridge_hooked = True

    if not getattr(stream_class, "_torrent_bridge_hooked", False):
        original_stream = stream_class.stream

        def stream(self, message, *, owner_id="web:local", session_id=None, request_id=None):
            if not is_explicit_parser_request(message):
                yield from original_stream(self, message, owner_id=owner_id,
                                           session_id=session_id, request_id=request_id)
                return
            yield from stream_parser_chat(self, message, owner_id=owner_id,
                                          session_id=session_id, request_id=request_id)

        stream_class.stream = stream
        stream_class._torrent_bridge_hooked = True
