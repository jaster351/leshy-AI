from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
import uuid
from typing import Any, Iterator

from .app import LeshyApp
from .diagnostics import event, problem
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow
from .telegram_fast import FastTelegramBridge


_REQUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_request_ledger(
    request_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    session_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('in_progress','done','failed')),
    response_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_request_owner_status
ON chat_request_ledger(owner_id,status,updated_at DESC);
"""

_LEASE_SECONDS = 600.0
_CONTEXT = threading.local()
_INSTALLED = False
_ORIGINAL_STREAM = None
_ORIGINAL_CHAT = None
_ORIGINAL_TELEGRAM_HANDLE = None


def _normal_request_id(value: str | None) -> str:
    return str(value or "").strip()[:180]


def _age_seconds(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return 0.0


class ChatRequestLedger:
    """Устойчивая защита от повторной доставки одного пользовательского запроса.

    Ledger хранится в основном SQLite LeshyBrain, поэтому работает не только
    внутри одного JS event loop, но и между потоками, интерфейсами и процессами,
    которые случайно используют один request_id.
    """

    def __init__(self, storage):
        self.storage = storage
        with self.storage.connection() as conn:
            conn.executescript(_REQUEST_SCHEMA)

    def get(self, request_id: str) -> dict[str, Any] | None:
        rid = _normal_request_id(request_id)
        if not rid:
            return None
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT request_id,owner_id,channel,session_id,status,response_json,error,created_at,updated_at "
                "FROM chat_request_ledger WHERE request_id=?",
                (rid,),
            ).fetchone()
        if row is None:
            return None
        try:
            response = json.loads(str(row["response_json"] or "{}"))
            if not isinstance(response, dict):
                response = {}
        except Exception:
            response = {}
        return {
            "request_id": str(row["request_id"]),
            "owner_id": str(row["owner_id"]),
            "channel": str(row["channel"]),
            "session_id": str(row["session_id"] or ""),
            "status": str(row["status"]),
            "response": response,
            "error": str(row["error"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def claim(
        self,
        request_id: str,
        *,
        owner_id: str,
        channel: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        rid = _normal_request_id(request_id)
        if not rid:
            raise ValueError("request_id пуст")
        owner = str(owner_id or "unknown").strip()[:200]
        channel_name = str(channel or "unknown").strip()[:80]
        sid = str(session_id or "").strip()[:200] or None
        now = utcnow()

        with self.storage.connection() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO chat_request_ledger"
                "(request_id,owner_id,channel,session_id,status,response_json,error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, owner, channel_name, sid, "in_progress", "{}", "", now, now),
            )
            if int(cur.rowcount or 0) == 1:
                event("chat_request_claimed", request_id=rid, owner_id=owner, channel=channel_name)
                return {"acquired": True, "request_id": rid, "status": "in_progress", "session_id": sid or ""}

            row = conn.execute(
                "SELECT owner_id,channel,session_id,status,response_json,error,updated_at "
                "FROM chat_request_ledger WHERE request_id=?",
                (rid,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Не удалось прочитать idempotency ledger")
            if str(row["owner_id"]) != owner:
                problem(
                    "chat_request_owner_collision",
                    "Один request_id получен от разных владельцев",
                    request_id=rid,
                    owner_id=owner,
                    previous_owner_id=str(row["owner_id"]),
                )
                raise PermissionError("request_id уже принадлежит другому пользователю")

            status = str(row["status"])
            stale = status == "in_progress" and _age_seconds(str(row["updated_at"])) >= _LEASE_SECONDS
            retryable = status == "failed" or stale
            if retryable:
                expected_status = status
                expected_updated = str(row["updated_at"])
                updated = conn.execute(
                    "UPDATE chat_request_ledger SET channel=?,session_id=COALESCE(?,session_id),status='in_progress',"
                    "response_json='{}',error='',updated_at=? "
                    "WHERE request_id=? AND status=? AND updated_at=?",
                    (channel_name, sid, now, rid, expected_status, expected_updated),
                )
                if int(updated.rowcount or 0) == 1:
                    event(
                        "chat_request_reclaimed",
                        request_id=rid,
                        owner_id=owner,
                        channel=channel_name,
                        previous_status=status,
                        stale=stale,
                    )
                    return {"acquired": True, "request_id": rid, "status": "in_progress", "session_id": sid or str(row["session_id"] or "")}

        current = self.get(rid) or {"status": status, "session_id": str(row["session_id"] or ""), "response": {}}
        event(
            "chat_duplicate_suppressed",
            request_id=rid,
            owner_id=owner,
            channel=channel_name,
            existing_status=str(current.get("status") or status),
        )
        return {
            "acquired": False,
            "request_id": rid,
            "status": str(current.get("status") or status),
            "session_id": str(current.get("session_id") or ""),
            "response": dict(current.get("response") or {}),
            "error": str(current.get("error") or ""),
        }

    def bind_session(self, request_id: str, session_id: str) -> None:
        rid = _normal_request_id(request_id)
        sid = str(session_id or "").strip()[:200]
        if not rid or not sid:
            return
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE chat_request_ledger SET session_id=?,updated_at=? WHERE request_id=? AND status='in_progress'",
                (sid, utcnow(), rid),
            )

    def complete(self, request_id: str, response: dict[str, Any] | None, *, session_id: str | None = None) -> None:
        rid = _normal_request_id(request_id)
        if not rid:
            return
        payload = dict(response or {})
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            encoded = "{}"
        sid = str(session_id or payload.get("session_id") or "").strip()[:200] or None
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE chat_request_ledger SET session_id=COALESCE(?,session_id),status='done',response_json=?,error='',updated_at=? "
                "WHERE request_id=?",
                (sid, encoded, utcnow(), rid),
            )
        event("chat_request_completed", request_id=rid, session_id=sid or "")

    def fail(self, request_id: str, error_text: str) -> None:
        rid = _normal_request_id(request_id)
        if not rid:
            return
        error_value = str(error_text or "")[:1000]
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE chat_request_ledger SET status='failed',error=?,updated_at=? WHERE request_id=?",
                (error_value, utcnow(), rid),
            )
        event("chat_request_failed", request_id=rid, error=error_value)


def ledger_for(app) -> ChatRequestLedger:
    ledger = getattr(app, "_chat_request_ledger", None)
    if isinstance(ledger, ChatRequestLedger):
        return ledger
    ledger = ChatRequestLedger(app.storage)
    setattr(app, "_chat_request_ledger", ledger)
    return ledger


def telegram_request_id(chat_id: Any, message_id: Any) -> str:
    return f"telegram:{str(chat_id).strip()}:{str(message_id).strip()}"[:180]


def _duplicate_stream_event(claim: dict[str, Any]) -> StreamEvent:
    response = dict(claim.get("response") or {})
    return StreamEvent(
        "duplicate",
        {
            "request_id": str(claim.get("request_id") or ""),
            "session_id": str(claim.get("session_id") or response.get("session_id") or ""),
            "status": str(claim.get("status") or "in_progress"),
            "duplicate": True,
        },
    )


def install_global_chat_idempotency() -> None:
    """Поставить общий anti-duplicate контракт поверх всех runtime-роутов.

    Устанавливать последним после остальных routing monkey-patches. Тогда Web,
    Windows shell, Telegram и прямой Runtime проходят через одну внешнюю защиту.
    """
    global _INSTALLED, _ORIGINAL_STREAM, _ORIGINAL_CHAT, _ORIGINAL_TELEGRAM_HANDLE
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_CHAT = LeshyApp.chat
    _ORIGINAL_TELEGRAM_HANDLE = FastTelegramBridge._handle_message

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        forced_id = _normal_request_id(getattr(_CONTEXT, "request_id", ""))
        rid = _normal_request_id(request_id) or forced_id or str(uuid.uuid4())
        preclaimed = bool(getattr(_CONTEXT, "preclaimed", False) and forced_id == rid)
        ledger = ledger_for(self.app)

        if not preclaimed:
            claim = ledger.claim(
                rid,
                owner_id=owner_id,
                channel="realtime-stream",
                session_id=session_id,
            )
            if not claim.get("acquired"):
                yield _duplicate_stream_event(claim)
                return

        done_payload: dict[str, Any] | None = None
        error_text = ""
        try:
            for item in _ORIGINAL_STREAM(
                self,
                message,
                owner_id=owner_id,
                session_id=session_id,
                request_id=rid,
            ):
                if item.type == "start":
                    sid = str(item.data.get("session_id") or "")
                    if sid:
                        ledger.bind_session(rid, sid)
                elif item.type == "done":
                    done_payload = dict(item.data)
                elif item.type == "error":
                    error_text = str(item.data.get("error") or "Ошибка генерации")
                yield item
        except Exception as exc:
            error_text = str(exc)
            ledger.fail(rid, error_text)
            raise
        else:
            if done_payload is not None:
                ledger.complete(rid, done_payload, session_id=str(done_payload.get("session_id") or session_id or ""))
            elif error_text:
                ledger.fail(rid, error_text)
            else:
                ledger.fail(rid, "Поток завершился без done-события")

    def chat(
        self: LeshyApp,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
        channel: str = "direct-chat",
    ) -> dict[str, Any]:
        rid = _normal_request_id(request_id)
        if not rid:
            return _ORIGINAL_CHAT(self, message, owner_id=owner_id, session_id=session_id)

        ledger = ledger_for(self)
        claim = ledger.claim(rid, owner_id=owner_id, channel=channel, session_id=session_id)
        if not claim.get("acquired"):
            cached = dict(claim.get("response") or {})
            cached.setdefault("session_id", str(claim.get("session_id") or session_id or ""))
            cached.setdefault("answer", "")
            cached.setdefault("sources", [])
            cached.update({
                "request_id": rid,
                "duplicate": True,
                "duplicate_status": str(claim.get("status") or "in_progress"),
            })
            return cached

        try:
            result = dict(_ORIGINAL_CHAT(self, message, owner_id=owner_id, session_id=session_id))
        except Exception as exc:
            ledger.fail(rid, str(exc))
            raise
        result["request_id"] = rid
        result["duplicate"] = False
        ledger.complete(rid, result, session_id=str(result.get("session_id") or session_id or ""))
        return result

    def telegram_handle(self: FastTelegramBridge, message: dict) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        sender = message.get("from") or {}
        user_id = str(sender.get("id") or "")
        if chat_id is None or message_id is None or not user_id:
            return _ORIGINAL_TELEGRAM_HANDLE(self, message)

        rid = telegram_request_id(chat_id, message_id)
        owner_id = f"telegram:{user_id}"
        session_id = f"telegram:{chat_id}:{user_id}"
        ledger = ledger_for(self.app)
        claim = ledger.claim(rid, owner_id=owner_id, channel="telegram", session_id=session_id)
        if not claim.get("acquired"):
            event(
                "telegram_duplicate_message_suppressed",
                chat_id=str(chat_id),
                message_id=int(message_id),
                request_id=rid,
                status=str(claim.get("status") or ""),
            )
            return

        previous_id = getattr(_CONTEXT, "request_id", None)
        previous_preclaimed = getattr(_CONTEXT, "preclaimed", False)
        _CONTEXT.request_id = rid
        _CONTEXT.preclaimed = True
        try:
            result = _ORIGINAL_TELEGRAM_HANDLE(self, message)
            current = ledger.get(rid)
            if current and current.get("status") == "in_progress":
                ledger.complete(rid, {"session_id": session_id, "telegram_delivered": True}, session_id=session_id)
            return result
        except Exception as exc:
            ledger.fail(rid, str(exc))
            raise
        finally:
            if previous_id is None:
                try:
                    delattr(_CONTEXT, "request_id")
                except AttributeError:
                    pass
            else:
                _CONTEXT.request_id = previous_id
            _CONTEXT.preclaimed = previous_preclaimed

    RealtimeChatManager.stream = stream
    LeshyApp.chat = chat
    FastTelegramBridge._handle_message = telegram_handle
    event("global_chat_idempotency_installed", lease_seconds=_LEASE_SECONDS)


__all__ = [
    "ChatRequestLedger",
    "install_global_chat_idempotency",
    "ledger_for",
    "telegram_request_id",
]
