from __future__ import annotations

from datetime import datetime, time as clock_time, timedelta, timezone
import re
import time
from typing import Any, Iterator

from .diagnostics import event, problem
from .learning_commands import execute_explicit_learning, is_explicit_learning_command
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow




# This asks about the *local agent's* learning, not about today's web news.
_LEARNING_REPORT_RE = re.compile(
    r"^(?:(?:леший|леша)[,!:\s]+)?(?:"
    r"что\s+(?:ты\s+)?(?:сегодня\s+)?(?:узнал|выучил|изучил)(?:\s+сегодня)?(?:\s+нового)?|"
    r"чему\s+(?:ты\s+)?(?:сегодня\s+)?научился(?:\s+сегодня)?|"
    r"что\s+(?:ты\s+)?(?:нового\s+)?выучил\s+сегодня"
    r")\s*[?!.]*$",
    re.IGNORECASE,
)


def is_learning_report_request(message: str) -> bool:
    return bool(_LEARNING_REPORT_RE.fullmatch(" ".join(str(message or "").split())))


def learning_report_answer(app) -> str:
    """Read-only local learning summary, bounded to the machine's calendar day.

    Only public web-source titles are shown. Do not expose private notes,
    other users' memories, or raw stored claims in a chat summary.
    """
    local_now = datetime.now().astimezone()
    today = local_now.date()
    zone = local_now.tzinfo
    since = datetime.combine(today, clock_time.min, tzinfo=zone).astimezone(timezone.utc).isoformat()
    until = datetime.combine(today + timedelta(days=1), clock_time.min, tzinfo=zone).astimezone(timezone.utc).isoformat()
    with app.storage.connection() as conn:
        rows = conn.execute(
            "SELECT status, topic, source_title FROM knowledge "
            "WHERE captured_at>=? AND captured_at<? "
            "AND (source_url LIKE 'https://%' OR source_url LIKE 'http://%') "
            "ORDER BY captured_at DESC LIMIT 100",
            (since, until),
        ).fetchall()
    counts: dict[str, int] = {}
    topics: list[str] = []
    seen: set[str] = set()
    for row in rows:
        status = str(row["status"] or "")
        counts[status] = counts.get(status, 0) + 1
        label = " ".join(str(row["source_title"] or row["topic"] or "").split())[:150]
        if label and label.casefold() not in seen and len(topics) < 5:
            seen.add(label.casefold())
            topics.append(label)
    enabled = bool(app.learning.is_enabled())
    if not rows:
        return (
            f"За сегодня ({today:%d.%m.%Y}) в локальной базе пока нет новых "
            "записей из публичных интернет-источников. "
            f"Автономное обучение {'включено' if enabled else 'выключено'}. "
            "Это не означает, что фоновые попытки поиска не выполнялись."
        )
    verified = counts.get("trusted", 0)
    candidates = counts.get("candidate", 0)
    lines = [
        f"За сегодня ({today:%d.%m.%Y}) в локальной базе сохранено "
        f"{len(rows)} записей из публичных интернет-источников "
        f"(подтверждённых: {verified}, ожидают проверки: {candidates}).",
        f"Автономное обучение {'включено' if enabled else 'выключено'}.",
    ]
    if topics:
        lines.append("Последние темы/материалы:\n" + "\n".join("• " + title for title in topics))
    if len(rows) == 100:
        lines.append("Показаны данные не более чем по 100 последним записям за день.")
    return "\n\n".join(lines)

_INSTALLED = False
_ORIGINAL_STREAM = None
_ORIGINAL_CHAT = None


def _save_direct_user(app, message: str, owner_id: str, session_id: str | None) -> str:
    sid = app._ensure_session(owner_id, session_id)
    now = utcnow()
    with app.storage.connection() as conn:
        conn.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
            (sid, "user", message, now),
        )
        count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
        if count == 1:
            conn.execute(
                "UPDATE sessions SET title=?,title_source=?,updated_at=? WHERE id=?",
                (" ".join(message.split())[:80], "auto", now, sid),
            )
    return sid


def install_explicit_learning_routing(app_class) -> None:
    """Подключить детерминированную маршрутизацию «изучи/научись».

    Метод устанавливается один раз на классах, поэтому его автоматически получают
    Web, голосовой Web, Telegram и совместимые будущие каналы, использующие
    RealtimeChatManager/LeshyApp.chat.
    """
    global _INSTALLED, _ORIGINAL_STREAM, _ORIGINAL_CHAT
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_CHAT = app_class.chat

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        value = str(message or "").strip()
        owner = (owner_id or "web:local").strip()[:200]
        report_requested = is_learning_report_request(value)
        if not report_requested and not is_explicit_learning_command(value):
            yield from _ORIGINAL_STREAM(
                self,
                value,
                owner_id=owner,
                session_id=session_id,
                request_id=request_id,
            )
            return

        if not value:
            raise ValueError("Сообщение пустое")
        rid, cancel = self._register(request_id)
        sid = ""
        started = time.monotonic()
        try:
            sid, _rows = self._save_user_and_history(value, owner, session_id)
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "learning_command": not report_requested})
            if not report_requested:
                yield StreamEvent("activity", {
                    "kind": "learning",
                    "phase": "queued",
                    "text": "Добавляю в обучение…",
                })
            if cancel.is_set():
                yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": "", "stopped": True, "learning_command": True})
                return

            if report_requested:
                answer = learning_report_answer(self.app)
                result = {"report": True, "answer": answer}
                event("learning_daily_report_generated", request_id=rid)
            else:
                result = execute_explicit_learning(self.app, owner, value)
                answer = str(result.get("answer") or "")
            answered_at = self.app._save_assistant(sid, answer)
            if answer and not cancel.is_set():
                for chunk in self._chunk_fallback(answer, 180):
                    if cancel.is_set():
                        break
                    yield StreamEvent("delta", {"text": chunk})
            if not report_requested:
                event(
                    "explicit_learning_command_queued",
                    request_id=rid,
                    session_id=sid,
                    owner_id=owner,
                    learning_enabled=bool(result.get("learning_enabled")),
                    research_tasks=len(result.get("research_task_ids") or []),
                    material_jobs=len(result.get("material_jobs") or []),
                )
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": answered_at,
                "sources": [],
                "stopped": bool(cancel.is_set()),
                "learning_command": True,
                "learning": result,
                "latency_ms": int((time.monotonic() - started) * 1000),
            })
        except Exception as exc:
            problem("explicit_learning_command_failed", str(exc), request_id=rid, session_id=sid, owner_id=owner)
            yield StreamEvent("error", {"request_id": rid, "session_id": sid, "error": str(exc), "learning_command": True})
        finally:
            self._unregister(rid)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None) -> dict[str, Any]:
        value = str(message or "").strip()
        owner = (owner_id or "web:local").strip()[:200]
        report_requested = is_learning_report_request(value)
        if not report_requested and not is_explicit_learning_command(value):
            return _ORIGINAL_CHAT(self, value, owner_id=owner, session_id=session_id)
        sid = _save_direct_user(self, value, owner, session_id)
        if report_requested:
            result = {"report": True, "answer": learning_report_answer(self)}
            event("learning_daily_report_generated", channel="chat")
        else:
            result = execute_explicit_learning(self, owner, value)
        answer = str(result.get("answer") or "")
        created_at = self._save_assistant(sid, answer)
        if not report_requested:
            event(
                "explicit_learning_command_queued",
                session_id=sid,
                owner_id=owner,
                learning_enabled=bool(result.get("learning_enabled")),
                research_tasks=len(result.get("research_task_ids") or []),
                material_jobs=len(result.get("material_jobs") or []),
            )
        return {"session_id": sid, "answer": answer, "created_at": created_at, "sources": [], "learning": result}

    RealtimeChatManager.stream = stream
    app_class.chat = chat


__all__ = ["install_explicit_learning_routing"]
