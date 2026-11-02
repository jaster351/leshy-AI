from __future__ import annotations

import queue
import threading
import time
from typing import Any, Iterator

from .diagnostics import event, problem
from .parcel_tracking import (
    BrowserSkillUnavailable,
    ParcelIntent,
    ParcelTrackingService,
    format_parcel_list,
    format_parcel_result,
    parse_parcel_intent,
)
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow


_INSTALLED = False
_ORIGINAL_STREAM = None
_ORIGINAL_CHAT = None
_ORIGINAL_STOP_BACKGROUND = None


def _service(app) -> ParcelTrackingService:
    service = getattr(app, "parcel_tracking", None)
    if isinstance(service, ParcelTrackingService):
        return service
    service = ParcelTrackingService(app)
    app.parcel_tracking = service
    service.start()
    return service


def _notification_route(owner_id: str, session_id: str | None) -> tuple[str, str]:
    sid = str(session_id or "")
    if sid.startswith("telegram:"):
        parts = sid.split(":", 2)
        if len(parts) >= 2 and parts[1]:
            return "telegram", parts[1]
    return "web", str(owner_id or "web:local")


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


def _execute_intent(app, intent: ParcelIntent, owner_id: str, session_id: str, *, activity=None) -> tuple[str, list[dict[str, str]]]:
    service = _service(app)
    if intent.action == "list":
        return format_parcel_list(service.list(owner_id)), []
    if intent.action == "stop":
        changed = service.stop_following(owner_id, intent.tracking_number)
        if changed:
            return f"Перестал следить за посылкой {intent.tracking_number}.", []
        return f"Посылка {intent.tracking_number} не была в активном отслеживании.", []

    if intent.action == "follow":
        channel, target = _notification_route(owner_id, session_id)
        result = service.follow(
            owner_id,
            intent.tracking_number,
            notify_channel=channel,
            notify_target=target,
            session_id=session_id,
            activity=activity,
        )
        return format_parcel_result(result, following=not result.needs_user_action), [
            {"title": "ГдеПосылка", "url": result.source_url}
        ] if result.source_url else []

    result = service.track_once(intent.tracking_number, activity=activity)
    return format_parcel_result(result), [
        {"title": "ГдеПосылка", "url": result.source_url}
    ] if result.source_url else []


def _run_intent_with_visible_activity(
    app,
    intent: ParcelIntent,
    owner_id: str,
    session_id: str,
    *,
    request_id: str,
    cancel: threading.Event,
) -> Iterator[StreamEvent]:
    """Выполнить browser-skill в worker-потоке и сразу отдавать человеку шаги.

    Без worker-потока callback browser-runner был бы виден только после полного
    завершения Playwright. Queue превращает реальные шаги браузера в SSE/Telegram
    activity-события в момент их выполнения.
    """
    updates: queue.Queue[tuple[str, Any]] = queue.Queue()
    result_box: dict[str, Any] = {}

    def activity(name: str, payload: dict) -> None:
        safe = {k: v for k, v in dict(payload or {}).items() if k in {"status", "skill_id", "browser", "elapsed_ms"}}
        text = str(safe.get("status") or "").strip()
        if text:
            updates.put(("activity", {
                "kind": "browser_skill",
                "phase": str(name or "browser_skill"),
                "text": text + ("…" if not text.endswith(("…", ".", "!", "?")) else ""),
            }))
        event("parcel_browser_activity", request_id=request_id, session_id=session_id, **safe)

    def worker() -> None:
        try:
            result_box["value"] = _execute_intent(app, intent, owner_id, session_id, activity=activity)
        except BaseException as exc:  # передаём исключение обратно generator-потоку
            result_box["error"] = exc
        finally:
            updates.put(("finished", None))

    thread = threading.Thread(target=worker, name=f"leshy-browser-skill-{request_id[:12]}", daemon=True)
    thread.start()

    finished = False
    while not finished:
        if cancel.is_set():
            yield StreamEvent("activity", {
                "kind": "browser_skill",
                "phase": "stopping",
                "text": "Останавливаю browser-навык…",
            })
            break
        try:
            kind, payload = updates.get(timeout=0.25)
        except queue.Empty:
            continue
        if kind == "activity":
            yield StreamEvent("activity", dict(payload or {}))
        elif kind == "finished":
            finished = True

    if not finished:
        # Playwright не всегда можно жёстко прервать из другого потока без
        # повреждения browser context. Поток daemon; результат после cancel
        # игнорируется, а UI получает stopped.
        return
    if "error" in result_box:
        raise result_box["error"]
    answer, sources = result_box.get("value") or ("", [])
    yield StreamEvent("browser_result", {"answer": answer, "sources": sources})


def install_parcel_tracking_routing(app_class, app=None) -> None:
    """Подключить browser-tracking как fast-path без LLM и без изменения Web transport."""
    global _INSTALLED, _ORIGINAL_STREAM, _ORIGINAL_CHAT, _ORIGINAL_STOP_BACKGROUND

    if app is not None:
        _service(app)
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_STOP_BACKGROUND = app_class.stop_background

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        value = str(message or "").strip()
        intent = parse_parcel_intent(value)
        if intent is None:
            yield from _ORIGINAL_STREAM(
                self,
                value,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

        owner = (owner_id or "web:local").strip()[:200]
        rid, cancel = self._register(request_id)
        sid = ""
        started = time.monotonic()
        try:
            sid, _rows = self._save_user_and_history(value, owner, session_id)
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "browser_skill": True, "parcel_tracking": True})
            if cancel.is_set():
                yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": "", "sources": [], "stopped": True})
                return

            answer = ""
            sources: list[dict[str, str]] = []
            if intent.action in {"track", "follow"}:
                for item in _run_intent_with_visible_activity(
                    self.app,
                    intent,
                    owner,
                    sid,
                    request_id=rid,
                    cancel=cancel,
                ):
                    if item.type == "browser_result":
                        answer = str(item.data.get("answer") or "")
                        sources = list(item.data.get("sources") or [])
                    else:
                        yield item
            else:
                answer, sources = _execute_intent(self.app, intent, owner, sid)

            if cancel.is_set():
                yield StreamEvent("done", {
                    "request_id": rid,
                    "session_id": sid,
                    "answer": "",
                    "sources": [],
                    "stopped": True,
                    "browser_skill": True,
                    "parcel_tracking": True,
                })
                return

            if answer:
                yield StreamEvent("activity", {
                    "kind": "browser_skill",
                    "phase": "answering",
                    "text": "Готовлю результат…",
                })
                for chunk in self._chunk_fallback(answer, 180):
                    if cancel.is_set():
                        break
                    yield StreamEvent("delta", {"text": chunk})
            stopped = cancel.is_set()
            answered_at = self.app._save_assistant(sid, answer) if answer else utcnow()
            latency_ms = int((time.monotonic() - started) * 1000)
            event(
                "parcel_chat_answered",
                request_id=rid,
                session_id=sid,
                owner_id=owner,
                action=intent.action,
                tracking_number=intent.tracking_number,
                latency_ms=latency_ms,
            )
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": answered_at,
                "sources": sources,
                "stopped": stopped,
                "browser_skill": True,
                "parcel_tracking": True,
                "latency_ms": latency_ms,
            })
        except BrowserSkillUnavailable as exc:
            answer = "Не удалось запустить браузерный навык отслеживания: " + str(exc)
            if sid:
                self.app._save_assistant(sid, answer)
            yield StreamEvent("delta", {"text": answer})
            yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": answer, "sources": [], "stopped": False, "parcel_tracking": True})
        except Exception as exc:
            problem("parcel_chat_stream_failed", str(exc), request_id=rid, session_id=sid, owner_id=owner)
            yield StreamEvent("error", {"request_id": rid, "session_id": sid, "error": str(exc), "parcel_tracking": True})
        finally:
            self._unregister(rid)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None) -> dict[str, Any]:
        value = str(message or "").strip()
        intent = parse_parcel_intent(value)
        if intent is None:
            return _ORIGINAL_CHAT(self, value, owner_id=owner_id, session_id=session_id)
        owner = (owner_id or "web:local").strip()[:200]
        sid = _save_direct_user(self, value, owner, session_id)
        try:
            answer, sources = _execute_intent(self, intent, owner, sid)
        except BrowserSkillUnavailable as exc:
            answer = "Не удалось запустить браузерный навык отслеживания: " + str(exc)
            sources = []
        created_at = self._save_assistant(sid, answer)
        event("parcel_chat_answered", session_id=sid, owner_id=owner, action=intent.action, tracking_number=intent.tracking_number)
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": created_at,
            "sources": sources,
            "browser_skill": True,
            "parcel_tracking": True,
        }

    def stop_background(self) -> None:
        service = getattr(self, "parcel_tracking", None)
        if isinstance(service, ParcelTrackingService):
            try:
                service.stop()
            except Exception as exc:
                problem("parcel_monitor_stop_failed", str(exc))
        return _ORIGINAL_STOP_BACKGROUND(self)

    RealtimeChatManager.stream = stream
    app_class.chat = chat
    app_class.stop_background = stop_background


__all__ = ["install_parcel_tracking_routing"]
