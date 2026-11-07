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
    "найди информацию в интернете", "найди эту информацию в интернете",
    "поищи информацию в интернете", "проверь информацию в интернете", "посмотри информацию в интернете",
    "найди в сети", "поищи в сети", "веб-поиск", "web search", "search the web",
    "выйди в интернет", "зайди в интернет", "сходи в интернет",
    "обратись к интернету", "проверь по интернету", "проверить через интернет",
)

# Пользователь естественно вставляет объект между глаголом и «в интернете»:
# «найди информацию про X в интернете», «проверь это в сети». Такие фразы
# должны детерминированно включать live-search и не зависеть от решения LLM.
_EXPLICIT_WEB_PATTERNS = (
    re.compile(r"\b(?:найди|поищи|проверь|посмотри)\b.{0,180}\b(?:в\s+интернете|в\s+сети|онлайн)\b", re.IGNORECASE),
    re.compile(r"\b(?:в\s+интернете|в\s+сети|онлайн)\b.{0,180}\b(?:найди|поищи|проверь|посмотри)\b", re.IGNORECASE),
    re.compile(r"\b(?:find|search|check|look\s+up)\b.{0,180}\b(?:online|on\s+the\s+web|on\s+the\s+internet)\b", re.IGNORECASE),
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
    свежих данных. При явной просьбе о веб-поиске он также обязателен.

    Важно: непроверенные candidate-знания не считаются достаточным основанием
    отказаться от интернета. Если для информационного запроса нет релевантного
    trusted-знания, Леший обязан выполнить live-search и только затем отвечать.
    """
    q = normalize_query(query)
    if not q:
        return None
    if any(marker in q for marker in _EXPLICIT_WEB_MARKERS) or any(pattern.search(q) for pattern in _EXPLICIT_WEB_PATTERNS):
        return "явный запрос интернет-поиска"
    if any(marker in q for marker in _FRESHNESS_MARKERS):
        return "требуются актуальные данные"
    if any(q == marker or q.startswith(marker + " ") for marker in _SMALL_TALK):
        return None

    if trusted_count <= 0:
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
            if candidate_count > 0:
                return "есть только непроверенные candidate-знания"
            return "пробел в локальной базе знаний"
    return None


def must_research(query: str, *, trusted_count: int = 0, candidate_count: int = 0) -> bool:
    return research_reason(query, trusted_count=trusted_count, candidate_count=candidate_count) is not None
