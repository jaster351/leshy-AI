from __future__ import annotations

"""Быстрое контекстное решение: нужен ли интернет ПРЯМО СЕЙЧАС.

Решение детерминированное, не зависит от даты обучения LLM.
Режим самообучения регулирует ЗАПИСЬ в базу, не возможность поиска.
"""

from datetime import datetime, timezone
import re
from typing import Any

from .research_policy import research_reason, is_casual_checkin, external_freshness_requested

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
_PLACES = ("ресторан", "отел", "гостиниц", "поездк", "путешеств")
_MEDICAL = ("дозировк", "противопоказан")
_SERVICE_AVAILABILITY = ("работает ли", "открыт ли", "доступен ли")
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
# Определение термина или принципа не является запросом актуального значения.
_DEFINITION_RE = re.compile(
    r"^(?:что такое|что значит|что означает|что представляет собой|what is|"
    r"как устроен(?:а|о|ы)?|как формируется|как работает|"
    r"объясни\s+(?:как|что|принцип))\s+"
)
# Признаки, из-за которых определение превращается в проверку свежих фактов.
_RECENCY_RE = re.compile(
    r"\b(?:сегодня|сейчас|вчера|завтра|последн\w*|актуальн\w*|текущ\w*|"
    r"нов(?:ый|ая|ое|ые|ую|ых)|новост\w*|свеж\w*|latest|current|today|202[5-9])\b"
)
_CREATIVE_ART_RE = re.compile(
    r"^(?:(?:придумай|сочини|напиши)\s+"
    r"(?:стих\w*|сказк\w*|истори\w*|песн\w*|рассказ\w*|поздравлен\w*|шутк\w*|тост)|"
    r"нарисуй\b|составь\s+(?:поздравлен\w*|пожелан\w*|тост))\b"
)



def _text(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def is_explicit_web_request(query: str) -> bool:
    q = _current_user_text(query).strip()
    return q.lower().startswith("/search ") or (
        research_reason(q, trusted_count=1, candidate_count=1) == "явный запрос интернет-поиска"
    )


def _current_user_text(query: str) -> str:
    """Для Telegram Reply классифицируем просьбу, а не старую цитату."""
    original = str(query or "").strip()
    marker = "[Текущий текст пользователя]"
    if marker in original:
        original = original.rsplit(marker, 1)[-1].strip()
    return original


def is_offline_creative_request(query: str) -> bool:
    """Писательские и поздравительные задачи не зависят от поисковиков.

    Исключения: пользователь просит веб-проверку или создание текста по
    новым *внешним* фактам — такие запросы требуют реального поиска.
    """
    current = _current_user_text(query)
    q = _text(current)
    # Пользователь может прислать готовое поздравление для редактирования.
    greeting = q.startswith((
        "с днем рождения", "с днём рождения", "поздравляю", "с праздником",
        "с новым годом", "с днем свадьбы", "с днём свадьбы",
    ))
    writing = any(q.startswith(prefix) for prefix in _OFFLINE_TASKS + (
        "поздравь ", "поздравить ", "перепиши ", "исправь текст",
        "улучши текст", "переделай текст", "сделай поздравлен",
        "сделай текст", "пожелай ",
    ))
    if not (greeting or writing):
        return False
    if is_explicit_web_request(current):
        return False

    # Художественный текст может упоминать погоду, билеты и новости:
    # такие слова — тема произведения, а не задание получить свежие данные.
    # Реальная подборка по новостям и явно запрошенный факт — исключения.
    external_facts = any(marker in q for marker in (
        "по последним новостям", "на основе последних новостей",
        "на основе актуальных данных", "с учетом свежих данных",
        "с учетом сегодняшних событий", "с актуальными фактами",
        "проверь сведения", "проверь по источникам",
    )) or bool(_EXACT_IDENT.search(current))
    if _CREATIVE_ART_RE.match(q) and not external_facts:
        return True

    # Чаще всего даже слова «новый», «версия», «новости» внутри наброска
    # поздравления описывают *текст*, а не задачу получения свежих фактов.
    greeting_content = greeting or any(
        part in q[:150] for part in (
            "поздравлен", "поздравительн", "пожелан", "тост",
            "с днем рождения", "с днём рождения", "с новым годом",
        )
    )
    if greeting_content:
        return not external_facts

    explicit_dynamic = (
        _REALTIME + _WEATHER + _NEWS + _VERSION + _SCHEDULE + _RULES
        + _SECURITY + _PLACES + _MEDICAL + ("цен", "стоимост", "покуп", "магазин")
    )
    if any(marker in q for marker in explicit_dynamic):
        return False
    if any(marker in q for marker in (
        "сегодня", "сейчас", "вчера", "завтра", "today", "right now",
        "последние события", "последние изменения",
    )):
        return False
    return True


def ttl_for_context(query: str) -> int:
    """Базовая актуальность 180 дней; для быстрых фактов существенно меньше."""
    q = _text(query)
    if any(x in q for x in _STABLE) and not _RECENCY_RE.search(q):
        return 2 * 365 * 24 * 60 * 60
    if any(x in q for x in _WEATHER):
        return 30 * 60
    if any(x in q for x in _REALTIME):
        return 15 * 60
    if any(x in q for x in _SERVICE_AVAILABILITY):
        return 15 * 60
    if any(x in q for x in _NEWS):
        return 60 * 60
    if any(x in q for x in _SECURITY):
        return 6 * 60 * 60
    if any(x in q for x in _PLACES):
        return 24 * 60 * 60
    if any(x in q for x in _SCHEDULE):
        return 6 * 60 * 60
    if any(x in q for x in _MEDICAL):
        return 7 * 24 * 60 * 60
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
    current = _current_user_text(query)
    q = _text(current)
    # Явное требование проверить интернет нельзя подавить даже свежим кэшем.
    if is_offline_creative_request(query):
        return None
    basic = research_reason(current, trusted_count=len(trusted), candidate_count=len(candidates))
    if is_explicit_web_request(current):
        return "явный запрос интернет-поиска"
    if is_casual_checkin(current):
        return None
    # Объяснение устойчивого термина можно сформулировать локально, если
    # не названы актуальный период, конкретная CVE/GHSA или внешний источник.
    if _DEFINITION_RE.match(q) and not _RECENCY_RE.search(q) and not _EXACT_IDENT.search(current) and not trusted and not candidates:
        return None
    # «Объясни закон Ома» — постоянный физический принцип, а не новая
    # редакция правового акта. Явное требование обновлений по-прежнему ищется.
    if any(x in q for x in _STABLE) and not _RECENCY_RE.search(q) and not _EXACT_IDENT.search(current) and not trusted and not candidates:
        return None
    # Задания на генерацию текста и объяснение общих принципов не должны
    # обращаться к интернету только из-за слов «новый» или «последний».
    # Исключение: явно актуальные внешние факты (новости, цены, CVE, законы).
    offline_task = any(q.startswith(prefix) for prefix in _OFFLINE_TASKS)
    time_sensitive = external_freshness_requested(current) or bool(_EXACT_IDENT.search(current))
    if offline_task and not time_sensitive:
        return None
    if _EXACT_IDENT.search(current):
        return "свежая проверка бюллетеня безопасности"
    if basic == "требуются актуальные данные":
        # Проверенная запись ещё внутри TTL: используем её без задержки
        # сетевого round-trip. Явный запрос интернета обработан выше.
        if trusted and any(knowledge_is_current(row, query) for row in trusted):
            return None
        return basic
    if any(x in q for x in _STABLE) and not _RECENCY_RE.search(q) and not _EXACT_IDENT.search(current):
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
    "adaptive_research_reason", "is_explicit_web_request", "is_offline_creative_request",
]
