from __future__ import annotations

import re
from typing import Any

from .diagnostics import event
from .freshness_memory_runtime import is_dynamic_query
from .knowledge import KnowledgeBase, is_fresh
from .llm import ChatMessage
from .realtime_chat import RealtimeChatManager


_STOP_TERMS = {
    "расскажи", "скажи", "какая", "какой", "какие", "сегодня", "сейчас", "текущая",
    "актуальная", "данные", "информация", "пожалуйста", "today", "current", "latest",
    "what", "which", "tell", "about", "please",
}

_INSTALLED = False
_ORIGINAL_SEARCH_PLAN = None
_ORIGINAL_BUILD_CONTEXT = None


def _terms(query: str) -> list[str]:
    values = re.findall(r"[0-9a-zа-яё]{3,}", str(query or "").lower())
    return [value for value in values if value not in _STOP_TERMS][:10]


def _search_fresh_dynamic(self: KnowledgeBase, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    if not is_dynamic_query(query):
        return []
    terms = _terms(query)
    with self.storage.connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM knowledge
            WHERE data_class='dynamic' AND status IN ('trusted','candidate')
            ORDER BY COALESCE(checked_at,captured_at) DESC,confidence DESC,id DESC
            LIMIT 1200
            """
        ).fetchall()
    scored: list[tuple[float, dict[str, Any]]] = []
    for raw in rows:
        row = dict(raw)
        if not is_fresh(row):
            continue
        haystack = f"{row.get('topic','')} {row.get('claim','')}".lower()
        matches = sum(1 for term in terms if term in haystack)
        if terms and matches <= 0:
            continue
        score = float(matches * 2) + float(row.get("confidence") or 0.0)
        if str(row.get("status")) == "trusted":
            score += 1.0
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in scored[: max(1, min(int(limit), 20))]]


def install_freshness_cache_runtime() -> None:
    global _INSTALLED, _ORIGINAL_SEARCH_PLAN, _ORIGINAL_BUILD_CONTEXT
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_SEARCH_PLAN = RealtimeChatManager._search_plan
    _ORIGINAL_BUILD_CONTEXT = RealtimeChatManager._build_context

    KnowledgeBase.search_fresh_dynamic = _search_fresh_dynamic

    def search_plan(self, message: str) -> dict[str, Any]:
        plan = dict(_ORIGINAL_SEARCH_PLAN(self, message))
        if not is_dynamic_query(message):
            return plan
        fresh = self.app.knowledge.search_fresh_dynamic(message, limit=5)
        plan["fresh_dynamic"] = fresh
        if fresh:
            plan["freshness_satisfied"] = True
            # Runtime-latency layer уважает этот флаг и не делает повторный
            # live-search, если пользователь не попросил явную перепроверку.
            plan["needed"] = False
            plan["reason"] = ""
            event("fresh_dynamic_cache_hit", rows=len(fresh), query=str(message)[:500])
        else:
            plan["freshness_satisfied"] = False
        return plan

    def build_context(self, message, owner_id, rows, *, search_plan=None):
        plan = search_plan or self._search_plan(message)
        messages, sources, attempted = _ORIGINAL_BUILD_CONTEXT(
            self,
            message,
            owner_id,
            rows,
            search_plan=plan,
        )
        fresh = list(plan.get("fresh_dynamic") or [])
        if fresh:
            facts = "\n".join(
                f"- {row.get('claim','')} (источник: {row.get('source_url','')}; проверено: {row.get('checked_at') or row.get('captured_at') or 'неизвестно'})"
                for row in fresh[:5]
            )
            messages.append(ChatMessage(
                "system",
                "Свежие динамические сведения из LeshyBrain. Они были проверены недавно и ещё находятся в пределах TTL. "
                "Используй их как текущие локальные данные. Если одновременно переданы результаты нового веб-поиска, "
                "новый веб-поиск имеет приоритет.\n" + facts,
            ))
        return messages, sources, attempted

    RealtimeChatManager._search_plan = search_plan
    RealtimeChatManager._build_context = build_context
    event("freshness_cache_runtime_installed")


__all__ = ["install_freshness_cache_runtime"]
