from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterator

from .diagnostics import event
from .knowledge import KnowledgeBase, fingerprint_claim, is_fresh
from .llm import ChatMessage
from .realtime_chat import RealtimeChatManager, StreamEvent
from .research import PROMPT_INJECTION_MARKERS, ResearchEngine
from .research_policy import research_reason
from .adaptive_internet_policy import is_offline_creative_request
from .storage import utcnow
from .temporal_grounding import temporal_grounding


# Эти данные меняются со временем и не должны становиться вечным знанием.
_DYNAMIC_MARKERS = (
    "погод", "weather", "курс валют", "курс доллара", "курс евро", "exchange rate",
    "цен", "стоим", "price", "новост", "news", "расписан", "schedule",
    "статус", "status", "последн", "latest", "current", "актуальн", "текущ",
    "релиз", "release", "версия", "version", "доступен", "работает ли", "открыт ли",
)
_STOP_TERMS = {
    "расскажи", "скажи", "какая", "какой", "какие", "сегодня", "сейчас", "текущая",
    "актуальная", "данные", "информация", "пожалуйста", "today", "current", "latest",
    "what", "which", "tell", "about", "please",
}

_INSTALLED = False
_ORIGINAL_RESEARCH_LIVE_QUERY = None
_ORIGINAL_SEARCH_PLAN = None
_ORIGINAL_BUILD_CONTEXT = None
_ORIGINAL_STREAM = None


def is_dynamic_query(query: str) -> bool:
    value = " ".join(str(query or "").lower().replace("ё", "е").split())
    if not value or is_offline_creative_request(query):
        return False
    if temporal_grounding(value) is not None:
        return True
    if any(marker in value for marker in _DYNAMIC_MARKERS):
        return True
    return research_reason(value, trusted_count=1, candidate_count=1) == "требуются актуальные данные"


def ttl_for_query(query: str) -> int:
    value = " ".join(str(query or "").lower().replace("ё", "е").split())
    if any(marker in value for marker in ("погод", "weather")):
        return 30 * 60
    if any(marker in value for marker in ("курс валют", "курс доллара", "курс евро", "exchange rate", "цен", "price", "статус", "status")):
        return 15 * 60
    if any(marker in value for marker in ("новост", "news")):
        return 30 * 60
    if any(marker in value for marker in ("расписан", "schedule")):
        return 60 * 60
    if any(marker in value for marker in ("версия", "version", "релиз", "release", "последн", "latest")):
        return 6 * 60 * 60
    return 60 * 60


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _row_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("checked_at", "verified_at", "captured_at"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _date_label(row: dict[str, Any]) -> str:
    stamp = _row_timestamp(row)
    return stamp.astimezone().strftime("%d.%m.%Y %H:%M") if stamp else "неизвестной даты"


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[0-9a-zа-яё]{3,}", str(query or "").lower())
    return [term for term in terms if term not in _STOP_TERMS][:10]


def _search_stale_dynamic(self: KnowledgeBase, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    if not is_dynamic_query(query):
        return []
    terms = _query_terms(query)
    with self.storage.connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM knowledge
            WHERE data_class='dynamic' AND status IN ('trusted','candidate','stale','conflict')
            ORDER BY COALESCE(checked_at,captured_at) DESC,id DESC
            LIMIT 1200
            """
        ).fetchall()
    scored: list[tuple[float, dict[str, Any]]] = []
    for raw in rows:
        row = dict(raw)
        if is_fresh(row):
            continue
        haystack = f"{row.get('topic','')} {row.get('claim','')}".lower()
        matches = sum(1 for term in terms if term in haystack)
        if terms and matches <= 0:
            continue
        score = float(matches * 2) + float(row.get("confidence") or 0.0)
        stamp = _row_timestamp(row)
        if stamp is not None:
            age_seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
            score += max(0.0, 0.5 - min(age_seconds, 86400 * 30) / (86400 * 60))
            row["freshness_at"] = stamp.isoformat()
            row["freshness_age_seconds"] = age_seconds
        row["freshness_label"] = _date_label(row)
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in scored[: max(1, min(int(limit), 20))]]


def _remember_dynamic_results(engine: ResearchEngine, query: str, result: dict[str, Any]) -> int:
    if not is_dynamic_query(query) or not result.get("results"):
        return 0
    now = utcnow()
    ttl = ttl_for_query(query)
    targets: list[tuple[str, str]] = []
    for item in result.get("results") or []:
        url = str(item.get("url") or "").strip()
        snippet = " ".join(str(item.get("snippet") or "").split()).strip()
        if not url or not snippet or any(marker in snippet.lower() for marker in PROMPT_INJECTION_MARKERS):
            continue
        claims = engine._extract_candidate_claims(snippet)
        if not claims and len(snippet) >= 25:
            claims = [snippet[:420]]
        for claim in claims[:2]:
            targets.append((url, fingerprint_claim(claim)))

    changed = 0
    with engine.storage.connection() as conn:
        for url, fingerprint in targets:
            row = conn.execute(
                """
                SELECT id FROM knowledge
                WHERE source_url=? AND fingerprint=?
                ORDER BY id DESC LIMIT 1
                """,
                (url, fingerprint),
            ).fetchone()
            if row is None:
                continue
            changed += conn.execute(
                """
                UPDATE knowledge
                SET data_class='dynamic',checked_at=?,ttl_seconds=?,captured_at=?
                WHERE id=?
                """,
                (now, ttl, now, int(row["id"])),
            ).rowcount
    if changed:
        engine.storage.audit(
            "knowledge.dynamic_live_search_remembered",
            {"query": query, "rows": changed, "checked_at": now, "ttl_seconds": ttl},
        )
        event("dynamic_live_search_remembered", query=query[:500], rows=changed, ttl_seconds=ttl)
    return int(changed)


def _migrate_legacy_dynamic(app) -> int:
    """Старые live-search записи о погоде/ценах могли ошибочно сохраниться static."""
    knowledge = getattr(app, "knowledge", None)
    if knowledge is None:
        return 0
    changed = 0
    with knowledge.storage.connection() as conn:
        rows = conn.execute(
            """
            SELECT id,topic,captured_at FROM knowledge
            WHERE data_class='static' AND source_type='live-search-snippet'
            ORDER BY id DESC LIMIT 3000
            """
        ).fetchall()
        for row in rows:
            topic = str(row["topic"] or "")
            if not is_dynamic_query(topic):
                continue
            checked_at = str(row["captured_at"] or "") or utcnow()
            changed += conn.execute(
                "UPDATE knowledge SET data_class='dynamic',checked_at=?,ttl_seconds=? WHERE id=?",
                (checked_at, ttl_for_query(topic), int(row["id"])),
            ).rowcount
    if changed:
        knowledge.storage.audit("knowledge.legacy_dynamic_migrated", {"rows": changed})
        event("legacy_dynamic_knowledge_migrated", rows=changed)
    return int(changed)


def _fallback_instruction(stale: list[dict[str, Any]]) -> str:
    facts = "\n".join(
        f"- {row.get('claim','')} (источник: {row.get('source_url','')}; проверено/получено: {row.get('freshness_label') or _date_label(row)})"
        for row in stale[:5]
    )
    return (
        "Свежие данные в интернете получить не удалось. Ниже находятся релевантные данные из локальной базы, "
        "но срок их актуальности истёк. Используй их только как запасной вариант. Обязательно прямо предупреди "
        "пользователя, что актуальные данные найти не удалось и сведения ниже могут быть устаревшими; укажи дату "
        "последней проверки/получения, если она известна. Не выдавай эти сведения как текущие.\n"
        "Устаревшие локальные сведения:\n" + facts
    )


class _TelegramFreshnessProxy:
    """Показывает status-activity в Telegram, не смешивая её с финальным ответом."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self._leshy_freshness_proxy = True

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def stream(self, *args, **kwargs) -> Iterator[StreamEvent]:
        status_preview = False
        for item in self._delegate.stream(*args, **kwargs):
            if item.type == "activity" and item.data.get("kind") == "freshness":
                status_preview = True
                yield StreamEvent("delta", {"text": str(item.data.get("text") or "Проверяю актуальность данных…")})
                continue
            if status_preview and item.type == "activity" and item.data.get("kind") == "web_search" and item.data.get("phase") == "searching":
                yield StreamEvent("delta", {"text": "\n\n" + str(item.data.get("text") or "Проверяю интернет…")})
                continue
            # После показа служебного статуса Telegram ждёт финальный done и заменяет
            # status-сообщение итоговым ответом. Так служебный текст не попадает в историю ответа.
            if status_preview and item.type == "delta":
                continue
            yield item


def _configure_app(app) -> None:
    if app is None:
        return
    if not bool(getattr(app, "_freshness_memory_migrated", False)):
        _migrate_legacy_dynamic(app)
        setattr(app, "_freshness_memory_migrated", True)
    telegram = getattr(app, "telegram", None)
    realtime = getattr(telegram, "_realtime", None) if telegram is not None else None
    if realtime is not None and not bool(getattr(realtime, "_leshy_freshness_proxy", False)):
        telegram._realtime = _TelegramFreshnessProxy(realtime)
        event("telegram_freshness_activity_enabled")


def install_freshness_memory_runtime(app=None) -> None:
    global _INSTALLED, _ORIGINAL_RESEARCH_LIVE_QUERY, _ORIGINAL_SEARCH_PLAN, _ORIGINAL_BUILD_CONTEXT, _ORIGINAL_STREAM

    if not _INSTALLED:
        _INSTALLED = True
        _ORIGINAL_RESEARCH_LIVE_QUERY = ResearchEngine.live_query
        _ORIGINAL_SEARCH_PLAN = RealtimeChatManager._search_plan
        _ORIGINAL_BUILD_CONTEXT = RealtimeChatManager._build_context
        _ORIGINAL_STREAM = RealtimeChatManager.stream

        KnowledgeBase.search_stale_dynamic = _search_stale_dynamic

        def live_query(self, query: str, reason: str = "запрос пользователя", limit: int = 5) -> dict:
            result = _ORIGINAL_RESEARCH_LIVE_QUERY(self, query, reason=reason, limit=limit)
            if bool(getattr(self, "auto_learning_enabled", lambda: True)()):
                _remember_dynamic_results(self, query, result)
            return result

        def search_plan(self, message: str) -> dict[str, Any]:
            plan = dict(_ORIGINAL_SEARCH_PLAN(self, message))
            if is_offline_creative_request(message):
                plan["needed"] = False
                plan["reason"] = ""
                plan["stale"] = []
                return plan
            stale = self.app.knowledge.search_stale_dynamic(message, limit=5) if is_dynamic_query(message) else []
            plan["stale"] = stale
            if stale:
                plan["needed"] = True
                if not plan.get("reason"):
                    plan["reason"] = "локальные данные устарели"
            return plan

        def build_context(self, message, owner_id, rows, *, search_plan=None, lookup_query=None):
            plan = search_plan or self._search_plan(message)
            messages, sources, attempted = _ORIGINAL_BUILD_CONTEXT(
                self,
                message,
                owner_id,
                rows,
                search_plan=plan,
                lookup_query=lookup_query,
            )
            stale = list(plan.get("stale") or [])
            if stale and not sources:
                messages.append(ChatMessage("system", _fallback_instruction(stale)))
            elif stale and sources:
                messages.append(ChatMessage(
                    "system",
                    "В локальной базе были устаревшие сведения по этому запросу, но свежий интернет-поиск дал результаты. "
                    "Используй свежие результаты как приоритетные; устаревшие сведения не выдавай как текущие.",
                ))
            return messages, sources, attempted

        def stream(self, message: str, *, owner_id: str = "web:local", session_id=None, request_id=None):
            plan = self._search_plan(str(message or ""))
            stale = list(plan.get("stale") or [])
            show_stale = bool(stale and plan.get("needed"))
            if show_stale:
                newest = max((_row_timestamp(row) for row in stale if _row_timestamp(row) is not None), default=None)
                date_text = newest.astimezone().strftime("%d.%m.%Y %H:%M") if newest else "неизвестной даты"
                yield StreamEvent("activity", {
                    "kind": "freshness",
                    "phase": "stale",
                    "text": f"В моей базе есть сведения по этому вопросу, но они устарели (последняя проверка: {date_text}).",
                    "stale_count": len(stale),
                })
                yield StreamEvent("activity", {
                    "kind": "web_search",
                    "phase": "searching",
                    "text": "Проверяю актуальную информацию в интернете…",
                    "reason": "локальные данные устарели",
                })
                event("stale_knowledge_visible", owner_id=str(owner_id), stale_count=len(stale), last_checked=date_text)

            for item in _ORIGINAL_STREAM(
                self,
                message,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            ):
                if show_stale and item.type == "activity" and item.data.get("kind") == "web_search" and item.data.get("phase") == "searching":
                    continue
                yield item

        ResearchEngine.live_query = live_query
        RealtimeChatManager._search_plan = search_plan
        RealtimeChatManager._build_context = build_context
        RealtimeChatManager.stream = stream
        event("freshness_memory_runtime_installed")

    _configure_app(app)


__all__ = [
    "install_freshness_memory_runtime",
    "is_dynamic_query",
    "ttl_for_query",
]
