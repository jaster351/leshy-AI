from __future__ import annotations

import re

# Границы слов важны: «оценка» не означает «цена», «дискурс» —
# «курс валют», а «правильно» — изменение законодательства.
_EXTERNAL_FRESH_RE = re.compile(
    r"\b(?:сейчас|сегодня|вчера|завтра|позавчера|"
    r"последн\w*|актуальн\w*|текущ\w*|свеж\w*|"
    r"нов(?:ый|ая|ое|ые|ую|ых)|"
    r"вышел|вышла|вышло|опубликовал\w*|опубликован\w*|"
    r"новост\w*|погод\w*|прогноз\w*|"
    r"цен(?:а|ы|е|у|ой|ам|ах)|стоимост\w*|сколько\s+стоит|"
    r"курс\s+(?:валют\w*|доллар\w*|евро|рубл\w*)|"
    r"расписан\w*|билет\w*|рейс\w*|"
    r"статус\w*|верси\w*|релиз\w*|обновлен\w*|"
    r"работает\s+ли|открыт\s+ли|доступен\s+ли|"
    r"закон\w*|правил(?:о|а|ы|ам|ах|ами|е|у)|"
    r"налог\w*|тариф\w*|штраф\w*|"
    r"выбор\w*|голосован\w*|президент\w*|"
    r"матч\w*|сч[её]т\s+матча|турнир\w*|"
    r"cve-\d{4}-\d{4,}|ghsa-[a-z0-9-]+|"
    r"latest|current|today|yesterday|tomorrow|"
    r"weather|forecast|price|news|status|version|release|schedule|"
    r"election\w*|score\w*|"
    r"new\s+(?:video|release|version|episode))\b",
    re.IGNORECASE,
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

_SMALL_TALK = frozenset((
    "привет", "здравствуй", "здравствуйте", "спасибо", "пока",
    "доброе утро", "добрый день", "добрый вечер", "доброй ночи",
    "hello", "hi", "thanks",
))

# Это вопросы о самом ассистенте, не о текущем состоянии внешнего мира.
# Полное совпадение обязательно: «что нового у Microsoft?» НЕ сюда.
_CASUAL_CHECKIN_RE = re.compile(
    r"^(?:ты\s+)?(?:чем\s+(?:ты\s+)?занят(?:а)?|"
    r"что\s+(?:ты\s+)?делаешь|чем\s+(?:ты\s+)?занимаешься)"
    r"(?:\s+(?:сейчас|вообще|сегодня))?\s*[?!.]*$"
)
_ASSISTANT_SOCIAL_RE = re.compile(
    r"^(?:как\s+(?:(?:у\s+тебя\s+)?дела|(?:твое|твоё)\s+настроение)|"
    r"что\s+у\s+тебя\s+нового|что\s+нового|"
    r"(?:ты\s+)?(?:тут|на\s+связи)|"
    r"(?:ты\s+)?меня\s+слышишь|"
    r"что\s+ты\s+умеешь|чем\s+ты\s+можешь\s+помочь|"
    r"кто\s+ты|расскажи\s+(?:мне\s+)?о\s+себе|"
    r"какая\s+(?:у\s+тебя|твоя)\s+версия|"
    r"какая\s+версия\s+лешего|"
    r"почему\s+ты\s+(?:не\s+)?отвечаешь|"
    r"почему\s+ты\s+не\s+отвечал)"
    r"(?:\s+(?:сейчас|сегодня|вообще))?\s*[?!.]*$"
)


def normalize_query(text: str) -> str:
    return " ".join(str(text).strip().lower().replace("ё", "е").split())


def _current_user_text(query: str) -> str:
    """Цитата в Telegram Reply не должна определять поиск для нового сообщения."""
    return str(query or "").rsplit("[Текущий текст пользователя]", 1)[-1].strip()


def is_casual_checkin(query: str) -> bool:
    """Разговор или вопрос о самом Лешем — не основание для веб-поиска."""
    q = normalize_query(_current_user_text(query))
    return bool(
        _CASUAL_CHECKIN_RE.fullmatch(q)
        or _ASSISTANT_SOCIAL_RE.fullmatch(q)
        or q.rstrip(" ?!.") in _SMALL_TALK
        or re.fullmatch(r"(?:привет|здравствуй|здравствуйте),?\s+(?:леший|леша)[!?.]*", q)
    )


def external_freshness_requested(query: str) -> bool:
    """Меняющиеся внешние сведения (не любое вхождение трёх букв)."""
    return bool(_EXTERNAL_FRESH_RE.search(normalize_query(_current_user_text(query))))


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
    current = _current_user_text(query)
    q = normalize_query(current)
    if not q:
        return None
    if q.startswith("/search ") or any(marker in q for marker in _EXPLICIT_WEB_MARKERS) or any(pattern.search(q) for pattern in _EXPLICIT_WEB_PATTERNS):
        return "явный запрос интернет-поиска"
    if is_casual_checkin(current):
        return None
    if external_freshness_requested(current):
        return "требуются актуальные данные"

    if trusted_count <= 0:
        # Не отправляем в интернет очевидную арифметику и совсем короткие реплики.
        if re.fullmatch(r"[\d\s+\-*/().,=]+", q):
            return None
        looks_informational = (
            "?" in current
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
