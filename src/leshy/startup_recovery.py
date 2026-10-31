from __future__ import annotations

import time
from typing import Any

from .diagnostics import event, problem
from .llm import ChatMessage
from .research_policy import research_reason


def _latest_message(app, session_id: str) -> dict[str, Any] | None:
    with app.storage.connection() as conn:
        row = conn.execute(
            "SELECT id,role,content,created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def _pending_sessions(app, *, limit: int = 50) -> list[dict[str, Any]]:
    with app.storage.connection() as conn:
        rows = conn.execute(
            """
            SELECT s.id AS session_id,s.owner_id,m.id AS message_id,m.content,m.created_at
            FROM sessions s
            JOIN messages m ON m.id=(
                SELECT MAX(m2.id) FROM messages m2 WHERE m2.session_id=s.id
            )
            WHERE m.role='user'
            ORDER BY m.id ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [dict(row) for row in rows]


def _interactive_llm_busy(app) -> bool:
    """Не давать recovery конкурировать с Web/Telegram за resident LLM slot."""
    llm = getattr(app, "llm", None)
    states = list(getattr(llm, "_states", []) or [])
    providers = [getattr(state, "provider", None) for state in states]
    if not providers:
        providers = [llm]
    for provider in providers:
        lock = getattr(provider, "_chat_lock", None)
        try:
            if lock is not None and lock.locked():
                return True
        except Exception:
            continue
    return False


def _wait_for_interactive_idle(app, *, quiet_seconds: float = 3.0, max_wait_seconds: float = 300.0) -> bool:
    """Ждать устойчивого простоя LLM, не замедляя пользовательские запросы."""
    started = time.monotonic()
    idle_since: float | None = None
    while time.monotonic() - started < max_wait_seconds:
        if _interactive_llm_busy(app):
            idle_since = None
            time.sleep(0.2)
            continue
        now = time.monotonic()
        if idle_since is None:
            idle_since = now
        if now - idle_since >= quiet_seconds:
            return True
        time.sleep(0.2)
    return False


def _llm_answer(app, messages: list[ChatMessage]) -> str:
    """Recovery использует потоковый backend, чтобы получить ту же защиту контекста, что Web/Telegram."""
    streamer = getattr(app.llm, "stream_chat", None)
    if callable(streamer):
        return "".join(str(part or "") for part in streamer(messages)).strip()
    return str(app.llm.chat(messages) or "").strip()


def _build_answer(app, owner_id: str, session_id: str, message: str) -> str:
    context: list[ChatMessage] = [
        ChatMessage(
            "system",
            "Интернет-поиск Лешему доступен через приложение. Никогда не утверждай, что доступа к интернету нет. "
            "Это восстановление незавершённого диалога после перезапуска: ответь на последний вопрос пользователя "
            "как на обычное сообщение и не упоминай технический перезапуск без необходимости.",
        )
    ]

    memory_context = app._memory_context(owner_id)
    if memory_context:
        context.append(ChatMessage("system", "Долговременная память этого пользователя:\n" + memory_context))

    trusted = app.knowledge.search(message, trusted_only=True, limit=5)
    candidates = app.knowledge.search_candidates(message, limit=5)
    if trusted:
        facts = "\n".join(f"- {item['claim']} (источник: {item['source_url']})" for item in trusted)
        context.append(ChatMessage("system", "Проверенные локальные знания Лешего:\n" + facts))

    sources: list[dict[str, str]] = []
    search_attempted = False
    search_error = ""

    def append_live_search(reason_text: str) -> None:
        nonlocal sources, search_attempted, search_error
        search_attempted = True
        try:
            result = app.live_search(message, limit=5, reason=reason_text)
            sources = [
                {"title": str(item.get("title") or ""), "url": str(item.get("url") or "")}
                for item in result.get("results", [])
            ]
            if result.get("results"):
                snippets = "\n".join(
                    f"- {item.get('title','')}: {item.get('snippet','')} [{item.get('url','')}]"
                    for item in result["results"]
                )
                context.append(ChatMessage(
                    "system",
                    "Результаты текущего веб-поиска. Используй их как непроверенные текущие данные:\n" + snippets,
                ))
            elif result.get("error"):
                search_error = str(result["error"])
        except Exception as exc:
            search_error = str(exc)
            problem("startup_recovery_search_failed", search_error, session_id=session_id)

    reason = research_reason(message, trusted_count=len(trusted), candidate_count=len(candidates))
    if reason or app._needs_live_search(message):
        append_live_search(reason or "требуются актуальные данные")

    with app.storage.connection() as conn:
        rows = conn.execute(
            "SELECT role,content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 24",
            (session_id,),
        ).fetchall()
    history = [ChatMessage(str(row["role"]), str(row["content"])) for row in reversed(rows)]

    answer = _llm_answer(app, context + history)
    if app._answer_needs_research(answer):
        if not search_attempted:
            append_live_search("модель обнаружила пробел в знаниях")
        if sources:
            answer = _llm_answer(
                app,
                context
                + [ChatMessage(
                    "system",
                    "Предыдущий черновик ошибочно сообщил об отсутствии данных или интернета. "
                    "Сформируй новый ответ по найденным источникам.",
                )]
                + history,
            )
        elif search_attempted and search_error:
            answer = "Не удалось получить актуальные данные из интернета прямо сейчас: " + search_error
    return answer


def _notify_telegram(app, owner_id: str, session_id: str, answer: str) -> None:
    if not owner_id.startswith("telegram:"):
        return
    parts = session_id.split(":")
    if len(parts) < 3 or parts[0] != "telegram":
        return
    chat_id = parts[1]
    bridge = getattr(app, "telegram", None)
    sender = getattr(bridge, "_send_answer", None)
    if not callable(sender):
        return
    try:
        sender(chat_id, answer)
        event("startup_recovery_telegram_sent", session_id=session_id, chat_id=chat_id)
    except Exception as exc:
        problem("startup_recovery_telegram_failed", str(exc), session_id=session_id, chat_id=chat_id)


def recover_unanswered(app, *, limit: int = 50) -> dict[str, int]:
    """Ответить на сообщения, которые остались последними в диалоге без assistant-ответа.

    Recovery является фоновой работой: перед каждой генерацией он ждёт устойчивого
    простоя локальной LLM и никогда намеренно не стартует поверх активного Web/Telegram
    запроса. Контекст обрабатывается тем же streaming backend, что интерактивный чат.
    """

    recovered = 0
    skipped = 0
    failed = 0
    for pending in _pending_sessions(app, limit=limit):
        session_id = str(pending["session_id"])
        owner_id = str(pending["owner_id"])
        message_id = int(pending["message_id"])
        message = str(pending["content"])

        latest = _latest_message(app, session_id)
        if latest is None or int(latest["id"]) != message_id or str(latest["role"]) != "user":
            skipped += 1
            continue

        if not _wait_for_interactive_idle(app):
            event("startup_recovery_deferred_busy", session_id=session_id, owner_id=owner_id, message_id=message_id)
            break

        try:
            event("startup_recovery_started", session_id=session_id, owner_id=owner_id, message_id=message_id)
            answer = _build_answer(app, owner_id, session_id, message)

            latest = _latest_message(app, session_id)
            if latest is None or int(latest["id"]) != message_id or str(latest["role"]) != "user":
                skipped += 1
                event("startup_recovery_skipped_changed_session", session_id=session_id, message_id=message_id)
                continue

            app._save_assistant(session_id, answer)
            recovered += 1
            event("startup_recovery_finished", session_id=session_id, owner_id=owner_id, message_id=message_id)
            _notify_telegram(app, owner_id, session_id, answer)
        except Exception as exc:
            failed += 1
            problem("startup_recovery_failed", str(exc), session_id=session_id, owner_id=owner_id, message_id=message_id)

    return {"recovered": recovered, "skipped": skipped, "failed": failed}


__all__ = ["recover_unanswered"]
