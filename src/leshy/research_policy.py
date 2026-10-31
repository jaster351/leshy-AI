from __future__ import annotations

import re

# Запросы, для которых ответ из локальной памяти нельзя считать актуальным.
_FRESHNESS_MARKERS = (
    "сейчас", "сегодня", "вчера", "завтра", "последн", "актуальн", "текущ",
    "новый", "новая", "новое", "новые", "свеж", "вышел", "вышла", "вышло",
    "опубликовал", "опубликовала", "опубликован", "опубликована",
    "новост", "погод", "курс", "валют", "цен", "стоим", "расписан", "статус",
    "версия", "релиз", "обновлен", "доступен", "работает ли", "открыт ли",
    "current", "latest", "today", "new video", "new release", "weather", "price", "news", "status", "version",
)

_EXPLICIT_WEB_MARKERS = (
    "найди в интернете", "поищи в интернете", "проверь в интернете", "посмотри в интернете",
    "найди в сети", "поищи в сети", "веб-поиск", "web search", "search the web",
)

_SMALL_TALK = (
    "привет", "здравствуй", "спасибо", "пока", "доброе утро", "добрый вечер",
    "как дела", "ты тут", "hello", "hi", "thanks",
)


def normalize_query(text: str) -> str:
    return " ".join(str(text).strip().lower().split())


def research_reason(
    query: str,
    *,
    trusted_count: int = 0,
    candidate_count: int = 0,
) -> str | None:
    """Возвращает причину обязательного live-search либо None.

    Политика детерминированная: модель не решает сама, нужен ли интернет для
    свежих данных. При явной просьбе о веб-поиске он также обязателен. Если
    локальная БЗ не содержит релевантного знания, информационный вопрос
    считается пробелом знаний и тоже уходит в live-search.
    """
    q = normalize_query(query)
    if not q:
        return None
    if any(marker in q for marker in _EXPLICIT_WEB_MARKERS):
        return "явный запрос интернет-поиска"
    if any(marker in q for marker in _FRESHNESS_MARKERS):
        return "требуются актуальные данные"
    if any(q == marker or q.startswith(marker + " ") for marker in _SMALL_TALK):
        return None

    if trusted_count <= 0 and candidate_count <= 0:
        # Не отправляем в интернет очевидную арифметику и совсем короткие реплики.
        if re.fullmatch(r"[\d\s+\-*/().,=]+", q):
            return None
        looks_informational = (
            "?" in query
            or q.startswith((
                "кто ", "что ", "где ", "когда ", "какой ", "какая ", "какие ",
                "как ", "почему ", "зачем ", "сколько ", "расскажи ", "объясни ",
                "who ", "what ", "where ", "when ", "how ", "why ",
            ))
        )
        if looks_informational:
            return "пробел в локальной базе знаний"
    return None


def must_research(query: str, *, trusted_count: int = 0, candidate_count: int = 0) -> bool:
    return research_reason(query, trusted_count=trusted_count, candidate_count=candidate_count) is not None
