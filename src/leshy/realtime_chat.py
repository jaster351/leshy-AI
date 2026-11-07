from __future__ import annotations

from dataclasses import dataclass
import threading
import time
import uuid
from typing import Any, Iterator

from .diagnostics import event, problem
from .cinema_research import cinema_query, cinema_instruction, cinema_search, cinema_no_data
from .inference_scheduler import get_inference_scheduler
from .llm import ChatMessage
from .research_policy import research_reason
from .storage import utcnow


@dataclass(slots=True)
class StreamEvent:
    type: str
    data: dict[str, Any]


class RealtimeChatManager:
    """Оркестрирует настоящую потоковую генерацию и её остановку пользователем."""

    def __init__(self, app, session_admin=None):
        self.app = app
        self.session_admin = session_admin
        self._lock = threading.RLock()
        self._cancellations: dict[str, threading.Event] = {}
        # Ранние web-stop запросы: пользователь нажал Стоп до того,
        # как сервер успел зарегистрировать request_id.
        self._pending_stop: dict[str, float] = {}

    def _register(self, request_id: str | None) -> tuple[str, threading.Event]:
        rid = (request_id or str(uuid.uuid4())).strip()[:120]
        cancel = threading.Event()
        with self._lock:
            expiry=self._pending_stop.pop(rid,0.0)
            if expiry>time.monotonic():
                cancel.set()
            self._cancellations[rid] = cancel
        return rid, cancel

    def stop(self, request_id: str) -> bool:
        rid = str(request_id or "").strip()
        if not rid:
            return False
        with self._lock:
            cancel = self._cancellations.get(rid)
            if cancel is None:
                if not rid.startswith("web:") or len(rid)>120:
                    return False
                # ВАЖНО: проверка активного запроса и ранний stop находятся
                # под одним lock. Иначе _register мог исполниться между ними,
                # и реальный поток оставался без cancel.
                now = time.monotonic()
                self._pending_stop = {
                    key:until for key,until in self._pending_stop.items()
                    if until>now
                }
                if len(self._pending_stop)>=128:
                    self._pending_stop.pop(next(iter(self._pending_stop)), None)
                self._pending_stop[rid] = now+30.0
                pending = True
            else:
                cancel.set()
                pending = False
        event(
            "chat_stream_early_stop_requested" if pending else "chat_stream_stop_requested",
            request_id=rid,
        )
        return True

    def _unregister(self, request_id: str) -> None:
        with self._lock:
            self._cancellations.pop(request_id, None)

    @staticmethod
    def _chunk_fallback(text: str, size: int = 48) -> Iterator[str]:
        value = str(text or "")
        for offset in range(0, len(value), size):
            yield value[offset:offset + size]

    def _llm_stream(self, messages: list[ChatMessage], cancel: threading.Event, owner_id: str) -> Iterator[str]:
        scheduler = get_inference_scheduler(self.app)
        priority = scheduler.priority_for(owner_id)
        source = str(owner_id or "system")[:160]
        with scheduler.slot(source=source, priority=priority, cancel_event=cancel):
            streamer = getattr(self.app.llm, "stream_chat", None)
            if callable(streamer):
                yield from streamer(messages, cancel_event=cancel)
                return
            answer = self.app.llm.chat(messages)
            yield from self._chunk_fallback(answer)

    def _save_user_and_history(self, message: str, owner_id: str, session_id: str | None) -> tuple[str, list[Any]]:
        sid = self.app._ensure_session(owner_id, session_id)
        now = utcnow()
        with self.app.storage.connection() as conn:
            if self.session_admin is not None and session_id:
                row = conn.execute("SELECT locked FROM sessions WHERE id=? AND owner_id=?", (sid, owner_id)).fetchone()
                if row is not None and bool(row["locked"]):
                    raise PermissionError("Этот чат заблокирован. Разблокируйте его в меню чата, чтобы продолжить переписку.")
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
            rows = conn.execute(
                "SELECT role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 24",
                (sid,),
            ).fetchall()
        return sid, rows

    def _search_plan(self, message: str) -> dict[str, Any]:
        trusted = self.app.knowledge.search(message, trusted_only=True, limit=5)
        candidates = self.app.knowledge.search_candidates(message, limit=5)
        reason = research_reason(message, trusted_count=len(trusted), candidate_count=len(candidates))
        needed = bool(reason or self.app._needs_live_search(message) or cinema_query(message))
        return {
            "trusted": trusted,
            "candidates": candidates,
            "reason": reason or ("требуются актуальные данные" if needed else ""),
            "needed": needed,
        }

    def _build_context(
        self,
        message: str,
        owner_id: str,
        rows: list[Any],
        *,
        search_plan: dict[str, Any] | None = None,
    ) -> tuple[list[ChatMessage], list[dict[str, str]], bool]:
        context: list[ChatMessage] = [
            ChatMessage(
                "system",
                "Интернет-поиск Лешему доступен через приложение. Никогда не утверждай, что доступа к интернету нет. "
                "Отвечай только готовым пользовательским ответом, без внутреннего анализа. Форматируй код fenced-блоками "
                "Markdown с языком, например ```python, чтобы Web и Telegram могли показать кнопку копирования.",
            )
        ]
        memory_context = self.app._memory_context(owner_id)
        if memory_context:
            context.append(ChatMessage("system", "Долговременная память этого пользователя:\n" + memory_context))

        plan = search_plan or self._search_plan(message)
        trusted = list(plan.get("trusted") or [])
        if trusted:
            facts = "\n".join(f"- {item['claim']} (источник: {item['source_url']})" for item in trusted)
            context.append(ChatMessage("system", "Проверенные локальные знания Лешего:\n" + facts))

        cinema_plan = cinema_query(message, rows)
        if cinema_plan is not None:
            context.append(ChatMessage("system", cinema_instruction(cinema_plan)))
        sources: list[dict[str, str]] = []
        search_attempted = bool(plan.get("needed")) or cinema_plan is not None
        if search_attempted:
            try:
                search_result = (cinema_search(self.app, cinema_plan) if cinema_plan is not None
                                 else self.app.live_search(message, limit=5, reason=str(plan.get("reason") or "требуются актуальные данные")))
                sources = [
                    {"title": str(item.get("title") or ""), "url": str(item.get("url") or "")}
                    for item in search_result.get("results", [])
                ]
                if search_result.get("results"):
                    snippets = "\n".join(
                        f"- {item.get('title','')}: {item.get('snippet','')} [{item.get('url','')}]"
                        for item in search_result["results"]
                    )
                    context.append(ChatMessage(
                        "system",
                        "Результаты текущего веб-поиска. Используй их для ответа, не выдавая непроверенное за факт:\n" + snippets,
                    ))
            except Exception as exc:
                problem("chat_stream_search_failed", str(exc), query=message[:500])
                context.append(ChatMessage("system", "Текущий веб-поиск временно не выполнен: " + str(exc)))

        history = [ChatMessage(str(row["role"]), str(row["content"])) for row in reversed(rows)]
        return context + history, sources, search_attempted

    def _stream_os_action(
        self,
        message: str,
        *,
        owner_id: str,
        session_id: str | None,
        request_id: str | None,
    ) -> Iterator[StreamEvent]:
        controller = getattr(self.app, "os_control", None)
        rid, cancel = self._register(request_id)
        sid = ""
        started = time.monotonic()
        try:
            sid, _rows = self._save_user_and_history(message, owner_id, session_id)
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "os_action": True})
            if cancel.is_set():
                yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": "", "stopped": True, "os_action": True})
                return

            response = controller.handle_user_request(message, owner_id=owner_id, session_id=sid)
            approval = response.get("approval") if isinstance(response, dict) else None
            answer = str(response.get("message") or "") if isinstance(response, dict) else str(response or "Системное действие не распознано.")
            if not cancel.is_set() and answer:
                for chunk in self._chunk_fallback(answer, 160):
                    if cancel.is_set():
                        break
                    yield StreamEvent("delta", {"text": chunk})
            stopped = cancel.is_set()
            answered_at = self.app._save_assistant(sid, answer) if answer else utcnow()
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": answered_at,
                "sources": [],
                "stopped": stopped,
                "os_action": True,
                "approval": approval,
                "latency_ms": int((time.monotonic() - started) * 1000),
            })
        except Exception as exc:
            problem("chat_stream_os_failed", str(exc), request_id=rid, owner_id=owner_id)
            yield StreamEvent("error", {"request_id": rid, "session_id": sid, "error": str(exc), "os_action": True})
        finally:
            self._unregister(rid)

    def stream(
        self,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        message = str(message or "").strip()
        owner_id = (owner_id or "web:local").strip()[:200]
        if not message:
            raise ValueError("Сообщение пустое")
        if len(message) > 32000:
            raise ValueError("Сообщение слишком длинное")

        controller = getattr(self.app, "os_control", None)
        if controller is not None and controller.is_request(message):
            yield from self._stream_os_action(
                message,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

        special = message.lower().startswith(("/remember ", "/memories")) or self.app._image_prompt(message) is not None
        if special:
            rid, cancel = self._register(request_id)
            try:
                if cancel.is_set():
                    yield StreamEvent("done", {
                        "request_id": rid, "session_id": session_id or "",
                        "answer": "", "stopped": True,
                    })
                    return
                result = self.app.chat(message, owner_id=owner_id, session_id=session_id)
                yield StreamEvent("start", {"request_id": rid, "session_id": result["session_id"]})
                answer = str(result.get("answer") or "")
                if not cancel.is_set() and answer:
                    yield StreamEvent("delta", {"text": answer})
                yield StreamEvent("done", {
                    "request_id": rid,
                    "session_id": result["session_id"],
                    "answer": answer,
                    "sources": result.get("sources") or [],
                    "image_job": result.get("image_job"),
                    "stopped": bool(cancel.is_set()),
                })
            finally:
                self._unregister(rid)
            return

        rid, cancel = self._register(request_id)
        sid = ""
        answer_parts: list[str] = []
        started = time.monotonic()
        try:
            sid, rows = self._save_user_and_history(message, owner_id, session_id)
            if cancel.is_set():
                yield StreamEvent("start", {"request_id": rid, "session_id": sid})
                yield StreamEvent("done", {"request_id": rid, "session_id": sid, "answer": "", "stopped": True})
                return
            plan = self._search_plan(message)
            if plan.get("needed"):
                yield StreamEvent("activity", {
                    "kind": "web_search",
                    "phase": "searching",
                    "text": "Ищу в интернете…",
                    "reason": str(plan.get("reason") or ""),
                })
                event("chat_web_search_visible", request_id=rid, session_id=sid, reason=str(plan.get("reason") or ""))

            messages, sources, search_attempted = self._build_context(message, owner_id, rows, search_plan=plan)
            cinema_plan = cinema_query(message, rows)
            if cinema_plan is not None and not sources:
                answer = cinema_no_data(cinema_plan)
                answered_at = self.app._save_assistant(sid, answer)
                yield StreamEvent("start", {"request_id": rid, "session_id": sid, "sources": []})
                if not cancel.is_set():
                    yield StreamEvent("delta", {"text": answer})
                yield StreamEvent("done", {
                    "request_id": rid, "session_id": sid, "answer": answer,
                    "created_at": answered_at, "sources": [], "stopped": bool(cancel.is_set()),
                    "cinema": True,
                })
                return
            if search_attempted:
                if sources:
                    yield StreamEvent("activity", {
                        "kind": "web_search",
                        "phase": "reading",
                        "text": f"Нашёл {len(sources)} источников · изучаю…",
                        "source_count": len(sources),
                        "sources": sources,
                    })
                else:
                    yield StreamEvent("activity", {
                        "kind": "web_search",
                        "phase": "finished",
                        "text": "Поиск завершён · подходящие источники не найдены",
                        "source_count": 0,
                    })

            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "sources": sources})
            event("chat_stream_started", request_id=rid, session_id=sid, owner_id=owner_id)

            # Актуальные сведения (погода, новости и т.п.) проверяем до публикации
            # текста: локальная модель иногда ошибочно заявляет, что интернет
            # недоступен, даже когда поиск уже выполнен. Иначе неверный ответ
            # успевает появиться в Telegram до финального редактирования.
            hold_search_answer = bool(search_attempted)
            for delta in self._llm_stream(messages, cancel, owner_id):
                if cancel.is_set():
                    break
                text = str(delta or "")
                if not text:
                    continue
                answer_parts.append(text)
                if not hold_search_answer:
                    yield StreamEvent("delta", {"text": text})

            answer = "".join(answer_parts).strip()
            stopped = cancel.is_set()
            if not stopped and search_attempted and self.app._answer_needs_research(answer):
                event("chat_stream_web_refusal_detected", request_id=rid, source_count=len(sources))
                if sources:
                    try:
                        instruction = ChatMessage(
                            "system",
                            "Предыдущий ответ ошибочно утверждал, что интернет недоступен. "
                            "В текущем контексте уже приведены найденные веб-источники. "
                            "Составь новый ответ только по этим источникам, без выдуманных "
                            "фактов и без предложения пользователю искать самому. "
                            "Если данных недостаточно, прямо сообщи, чего именно не хватает.",
                        )
                        retry_parts = []
                        for delta in self._llm_stream(messages + [instruction], cancel, owner_id):
                            if cancel.is_set():
                                break
                            retry_parts.append(str(delta or ""))
                        if not cancel.is_set():
                            repaired = "".join(retry_parts).strip()
                            if repaired and not self.app._answer_needs_research(repaired):
                                answer = repaired
                            else:
                                answer = (
                                    "Я нашёл веб-источники, но не смог получить из них "
                                    "достаточно надёжных данных для ответа."
                                )
                    except Exception as exc:
                        problem("chat_stream_web_refusal_retry_failed", str(exc), request_id=rid)
                        answer = "Интернет-поиск выполнен, но достоверный ответ сформировать не удалось."
                else:
                    failure_marker = "Текущий веб-поиск временно не выполнен:"
                    failure = next(
                        (
                            str(item.content).split(failure_marker, 1)[-1].strip()
                            for item in messages
                            if item.role == "system" and failure_marker in str(item.content)
                        ),
                        "",
                    )
                    answer = (
                        "Не удалось получить актуальные данные из интернета: " + failure
                        if failure else
                        "Я попробовал найти актуальную информацию в интернете, "
                        "но подходящих подтверждённых источников не нашёл."
                    )
                event("chat_stream_web_refusal_handled", request_id=rid, source_count=len(sources))
            if hold_search_answer and not cancel.is_set():
                for part in self._chunk_fallback(answer, 160):
                    yield StreamEvent("delta", {"text": part})
            stopped = cancel.is_set()
            if answer and not stopped:
                answered_at = self.app._save_assistant(sid, answer)
            else:
                answered_at = utcnow()
            latency_ms = int((time.monotonic() - started) * 1000)
            event(
                "chat_stream_finished",
                request_id=rid,
                session_id=sid,
                owner_id=owner_id,
                answer_chars=len(answer),
                stopped=stopped,
                latency_ms=latency_ms,
                web_search=search_attempted,
            )
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": answered_at,
                "sources": sources,
                "stopped": stopped,
                "latency_ms": latency_ms,
            })
        except Exception as exc:
            problem("chat_stream_failed", str(exc), request_id=rid, session_id=sid, owner_id=owner_id)
            yield StreamEvent("error", {"request_id": rid, "session_id": sid, "error": str(exc)})
        finally:
            self._unregister(rid)


__all__ = ["RealtimeChatManager", "StreamEvent"]
