"""Route only explicit owner commands to Parser for Plex.

Works in both HTTP chat and streaming chat; other conversations are unchanged.
"""
from __future__ import annotations

from .torrent_parser_bridge import TorrentParserBridge, parse_request, parse_premiere_request
from .storage import utcnow

def authorized_owner_for_parser(app, bridge, owner_id: str) -> str:
    """Use a verified bot owner for the one configured Web parser; never trust chat text."""
    value = str(owner_id or "").strip()
    if bridge.owner_allowed(value):
        return value
    if not value.startswith("telegram:"):
        return value
    telegram_id = value.partition(":")[2].strip()
    bot = getattr(app, "telegram", None)
    checker = getattr(bot, "_is_owner", None)
    if not telegram_id or not callable(checker):
        return value
    try:
        if not checker(telegram_id):
            return value
    except Exception:
        return value
    # The Telegram bot owner is configured locally in telegram_access.json.
    # Do not hand this credential to general Telegram users or unmatched Web owners.
    configured_owner = str(bridge._read().get("owner_id") or "").strip()
    return configured_owner if bridge.owner_allowed(configured_owner) else value



def install_torrent_parser_chat(app_class, stream_class):
    if not getattr(app_class, "_torrent_bridge_hooked", False):
        original_chat = app_class.chat

        def chat(self, message, *, owner_id="web:local", session_id=None):
            if parse_request(message) is None and parse_premiere_request(message) is None:
                return original_chat(self, message, owner_id=owner_id, session_id=session_id)
            bridge = TorrentParserBridge(self.settings.brain_dir)
            parser_owner = authorized_owner_for_parser(self, bridge, owner_id)
            answer = bridge.execute_chat(parser_owner, message)
            sid = self._ensure_session(owner_id, session_id)
            created = utcnow()
            with self.storage.connection() as db:
                db.execute(
                    "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                    (sid, "user", str(message), created),
                )
                db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (created, sid))
            completed = self._save_assistant(sid, answer)
            return {"session_id": sid, "answer": answer,
                    "created_at": completed, "sources": []}

        app_class.chat = chat
        app_class._torrent_bridge_hooked = True

    if not getattr(stream_class, "_torrent_bridge_hooked", False):
        original_stream = stream_class.stream

        def stream(self, message, *, owner_id="web:local", session_id=None, request_id=None):
            if parse_request(message) is None and parse_premiere_request(message) is None:
                yield from original_stream(self, message, owner_id=owner_id,
                                           session_id=session_id, request_id=request_id)
                return
            from .realtime_chat import StreamEvent
            rid, cancel = self._register(request_id)
            try:
                if cancel.is_set():
                    yield StreamEvent("done", {"request_id": rid,
                                               "session_id": session_id or "",
                                               "answer": "", "stopped": True})
                    return
                result = self.app.chat(message, owner_id=owner_id, session_id=session_id)
                answer = str(result.get("answer") or "")
                yield StreamEvent("start", {"request_id": rid, "session_id": result["session_id"]})
                if not cancel.is_set():
                    yield StreamEvent("delta", {"text": answer})
                yield StreamEvent("done", {
                    "request_id": rid, "session_id": result["session_id"],
                    "answer": answer, "sources": [], "stopped": bool(cancel.is_set())
                })
            finally:
                self._unregister(rid)

        stream_class.stream = stream
        stream_class._torrent_bridge_hooked = True
