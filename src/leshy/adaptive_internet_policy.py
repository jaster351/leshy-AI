from __future__ import annotations

"""Быстрое контекстное решение: нужен ли интернет ПРЯМО СЕЙЧАС.

Решение детерминированное, не зависит от даты обучения LLM.
Режим самообучения регулирует ЗАПИСЬ в базу, не возможность поиска.
"""

from datetime import datetime, timezone
import re
from typing import Any

from .research_policy import research_reason

SIX_MONTHS_SECONDS = 180 * 24 * 60 * 60

_EXPLICIT_FRESH = (
    "сегодня", "сейчас", "вчера", "завтра", "последн", "актуальн",
    "новост", "новый релиз", "свеж", "текущ", "проверь обновлен",
    "today", "yesterday", "tomorrow", "latest", "current", "breaking news",
)
_REALTIME = ("курс валют", "курс доллара", "курс евро", "бирж", "котировк", "exchange rate", "акци", "криптовалют", "bitcoin", "btc", "price now")
_WEATHER = ("погод", "weather", "температура на улице", "прогноз")
_NEWS = ("новост", "news", "происшеств", "вышел", "опубликовал", "опубликована")
_VERSION = ("версия", "релиз", "обновлен", "патч", "release", "version", "changelog")
_SCHEDULE = ("расписан", "авиарейс", "поезд", "расписание", "schedule", "билет", "рейс")
_RULES = ("закон", "правил", "регламент", "штраф", "налог", "санкц", "пдд", "гост", "постановлен", "тариф")
_SECURITY = ("cve-", "уязвим", "exploit", "vulnerab", "security advisory", "patch tuesday")
_STABLE = (
    "таблиц умножен", "теорема пифагора", "закон ома", "принцип архимеда",
    "древн", "древнегреческ", "формула площади", "what is addition",
)
_OFFLINE_TASKS = (
    "напиши ", "составь ", "перефразируй ", "переведи ",
    "придумай ", "нарисуй ", "создай текст ", "сочини ",
    "почему ", "как приготовить ", "как объяснить ", "помоги написать ",
)
_EXACT_IDENT = re.compile(r"\b(?:CVE-\d{4}-\d{4,}|GHSA-[0-9a-z-]+)\b", re.I)


def _text(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def is_explicit_web_request(query: str) -> bool:
    q = str(query or "").strip()
    return q.lower().startswith("/search ") or (
        research_reason(q, trusted_count=1, candidate_count=1) == "явный запрос интернет-поиска"
    )


def ttl_for_context(query: str) -> int:
    """Базовая актуальность 180 дней; для быстрых фактов существенно меньше."""
    q = _text(query)
    if any(x in q for x in _STABLE):
        return 2 * 365 * 24 * 60 * 60
    if any(x in q for x in _WEATHER):
        return 30 * 60
    if any(x in q for x in _REALTIME):
        return 15 * 60
    if any(x in q for x in _NEWS):
        return 60 * 60
    if any(x in q for x in _SECURITY):
        return 6 * 60 * 60
    if any(x in q for x in _SCHEDULE):
        return 6 * 60 * 60
    if any(x in q for x in _VERSION):
        return 24 * 60 * 60
    if any(x in q for x in _RULES):
        return 7 * 24 * 60 * 60
    return SIX_MONTHS_SECONDS


def knowledge_is_current(row: dict[str, Any], query: str, *, now: datetime | None = None) -> bool:
    """Кандидат остаётся непроверенным независимо от срока; этот метод о возрасте."""
    stamp = row.get("checked_at") or row.get("verified_at") or row.get("captured_at")
    if not stamp:
        return False
    try:
        captured = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        age = (current - captured).total_seconds()
        limit = ttl_for_context(query)
        if str(row.get("data_class") or "static") == "dynamic":
            stored_ttl = int(row.get("ttl_seconds") or 0)
            limit = min(limit, max(0, stored_ttl))
        return 0 <= age <= limit
    except (TypeError, ValueError, OverflowError):
        return False


def adaptive_research_reason(query: str, trusted: list[dict] | None = None, candidates: list[dict] | None = None) -> str | None:
    trusted = list(trusted or [])
    candidates = list(candidates or [])
    q = _text(query)
    # Явное требование проверить интернет нельзя подавить даже свежим кэшем.
    basic = research_reason(query, trusted_count=len(trusted), candidate_count=len(candidates))
    if is_explicit_web_request(query):
        return "явный запрос интернет-поиска"
    # Задания на генерацию текста и объяснение общих принципов не должны
    # обращаться к интернету только из-за слов «новый» или «последний».
    # Исключение: явно актуальные внешние факты (новости, цены, CVE, законы).
    offline_task = any(q.startswith(prefix) for prefix in _OFFLINE_TASKS)
    time_sensitive = any(
        marker in q for marker in
        _REALTIME + _WEATHER + _NEWS + _VERSION + _SCHEDULE + _RULES + _SECURITY
    ) or any(marker in q for marker in ("сегодня", "сейчас", "вчера", "завтра", "today", "right now"))
    if offline_task and not time_sensitive:
        return None
    if basic == "требуются актуальные данные":
        # Проверенная запись ещё внутри TTL: используем её без задержки
        # сетевого round-trip. Явный запрос интернета обработан выше.
        if trusted and any(knowledge_is_current(row, query) for row in trusted):
            return None
        return basic
    if _EXACT_IDENT.search(query):
        return "свежая проверка бюллетеня безопасности"
    if any(x in q for x in _STABLE) and not trusted and not candidates:
        return None
    if any(marker in q for marker in _SECURITY):
        return "свежие данные по безопасности"
    if any(marker in q for marker in _EXPLICIT_FRESH):
        return "требуются актуальные данные"

    # Дата берётся из ОС. Год рядом с текущим годом требует проверки для
    # событий/персоналий, а исторический год не означает обязательную сеть.
    year = datetime.now().year
    years = [int(value) for value in re.findall(r"\b20\d{2}\b", q)]
    if years and max(years) >= year - 1 and any(x in q for x in ("cve", "событ", "новост", "президент", "релиз", "закон", "уязвим")):
        return "события требуют актуального источника"

    if trusted:
        if any(knowledge_is_current(row, query) for row in trusted):
            return None
        return "срок актуальности проверенных знаний истёк"
    if candidates:
        return "есть только непроверенные сведения"

    # Общеизвестные определения можно объяснить локально. Если вопрос
    # содержит современный год/версию/CVE, предыдущие проверки уже заставили
    # использовать интернет.
    if q.startswith(("что такое ", "what is ")) and not re.search(r"\b20\d{2}\b", q):
        return None
    return basic


__all__ = [
    "SIX_MONTHS_SECONDS", "ttl_for_context", "knowledge_is_current",
    "adaptive_research_reason", "is_explicit_web_request",
]
