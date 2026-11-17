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
    r"закон(?:а|ы|у|е|ом|ов|ами|ах|одател\w*)?|правил(?:о|а|ы|ам|ах|ами|е|у)|"
    r"налог\w*|тариф\w*|штраф\w*|"
    r"выбор\w*|голосован\w*|президент\w*|"
    r"матч\w*|сч[её]т\s+матча|турнир\w*|"
    r"поездк\w*|путешеств\w*|ресторан\w*|отел\w*|гостиниц\w*|"
    r"дозировк\w*|противопоказан\w*|"
    r"гендиректор\w*|министр\w*|мэр\w*|"
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

# A prohibition on internet access is not an explicit WEB request.
# It must take priority over "проверь в интернете" quoted/negated in a prompt.
_OFFLINE_DIRECTIVE_RE = re.compile(
    r"(?:не\s+(?:используй|используйте|ищи|ищите|проверяй|проверяйте|"
    r"обращайся|обращайтесь|заходи|заходите)\s+(?:(?:по|через|в)\s+)?"
    r"(?:интернет\w*|сеть|веб\w*|поиск\w*)|"
    r"(?:без|никакого)\s+(?:интернет\w*|веб[-\s]?поиск\w*|поиск\w*\s+в\s+сети)|"
    r"интернет\s+не\s+используй|"
    r"(?:поиск|интернет)\s+(?:не\s+нужен|не\s+использовать))",
    re.IGNORECASE,
)


def is_explicit_offline_request(query: str) -> bool:
    return bool(_OFFLINE_DIRECTIVE_RE.search(_current_user_text(query)))


# Short insults, jokes, laughter and emotional reactions are conversation,
# not external facts. Keep this whole-utterance-only: "ты лох, узнай погоду"
# must still request a real weather lookup.
_SOCIAL_REACTION_RE = re.compile(
    r"^(?:(?:ты|леший|леша|вы)\s+)?"
    r"(?:лох|лохушк\w*|дурак|дура|дурачок|тупой|тупица|идиот|"
    r"глупый|глупыш|бестолоч\w*|зануда|клоун|"
    r"молодец|красавчик|умница|супер|крутой|"
    r"смешной|прикольный|забавный|"
    r"достал|надоел|тормоз|отстой|отстань|замолчи|заткнись|"
    r"обиделся|обидно|обидел|"
    r"хаха+|ахаха+|ахах+|лол|lol|хех|хм+|мм+|"
    r"шучу|шутка|это\s+шутка|"
    r"пошутил|не\s+злись|не\s+обижайся)"
    r"(?:\s+(?:ты|леший|леша))?"
    r"\s*[!?.…\s)]*$",
    re.IGNORECASE,
)


def is_social_utterance(query: str) -> bool:
    text = normalize_query(_current_user_text(query))
    text = re.sub(r"^(?:(?:слушай|леший|леша|эй)\s*[,!:]\s*)+", "", text)
    return bool(_SOCIAL_REACTION_RE.fullmatch(text)) and len(text) <= 100


_SMALL_TALK = frozenset((
    "привет", "здравствуй", "здравствуйте", "спасибо", "пока",
    "доброе утро", "добрый день", "добрый вечер", "доброй ночи",
    "hello", "hi", "thanks",
))

# Это вопросы о самом ассистенте, не о текущем состоянии внешнего мира.
# Полное совпадение обязательно: «что нового у Microsoft?» НЕ сюда.
_CASUAL_CHECKIN_RE = re.compile(
    r"^(?:ты\s+)?(?:чем\s+(?:ты\s+)?занят(?:а)?|"
    r"что\s+(?:ты\s+)?делаешь|что\s+ты\s+сейчас\s+делаешь|"
    r"чем\s+(?:ты\s+)?занимаешься)"
    r"(?:\s+(?:сейчас|вообще|сегодня))?\s*[?!.]*$"
)
_ASSISTANT_SOCIAL_RE = re.compile(
    r"^(?:как\s+(?:(?:у\s+тебя\s+)?дела|(?:твое|твоё)\s+настроение)|"
    r"что\s+у\s+тебя\s+нового|что\s+нового\s+у\s+тебя|что\s+нового|"
    r"как\s+твои\s+дела|кто\s+я|"
    r"(?:ты\s+)?(?:тут|на\s+связи)|"
    r"(?:ты\s+)?меня\s+слышишь|"
    r"что\s+ты\s+умеешь|чем\s+ты\s+можешь\s+помочь|"
    r"кто\s+ты|расскажи\s+(?:мне\s+)?о\s+себе|"
    r"какая\s+(?:у\s+тебя|твоя)\s+(?:сейчас\s+)?версия|"
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


# Short conversational prompts about Leshy's day are not requests for news.
# Match the entire utterance: "как сегодня погода?" must remain a web query.
_TODAY_CONVERSATION_RE = re.compile(
    r"^(?:как\s+сегодня(?:\s+(?:дела|настроение))?|"
    r"как\s+(?:ты|у\s+тебя)\s+сегодня|"
    r"как\s+прош[её]л\s+(?:твой\s+)?день(?:\s+сегодня)?|"
    r"что\s+(?:ты\s+)?сегодня\s+(?:делал|делаешь))\s*[?!.]*$",
    re.IGNORECASE,
)


def is_casual_checkin(query: str) -> bool:
    """Разговор или вопрос о самом Лешем — не основание для веб-поиска."""
    q = normalize_query(_current_user_text(query))
    # Обращение или вводное слово не меняет смысл короткого вопроса.
    q = re.sub(r"^(?:(?:слушай|леший|леша|эй)\s*[,!:]\s*)+", "", q)
    return bool(
        is_social_utterance(q)
        or _TODAY_CONVERSATION_RE.fullmatch(q)
        or _CASUAL_CHECKIN_RE.fullmatch(q)
        or _ASSISTANT_SOCIAL_RE.fullmatch(q)
        or q.rstrip(" ?!.") in _SMALL_TALK
        or re.fullmatch(r"(?:привет|здравствуй|здравствуйте),?\s+(?:леший|леша)[!?.]*", q)
    )


# Узкие вопросы о происхождении Лешего относятся к локальной идентичности,
# закреплённой в коде. Это только решение о маршруте, НЕ шаблон ответа.
_CREATOR_LOCAL_RE = re.compile(
    r"^(?:кто\s+(?:(?:твой|у\s+тебя)\s+)?(?:создатель|автор|разработчик)(?:\s+лешего)?|"
    r"кто\s+(?:тебя|лешего)\s+(?:создал|сделал)|"
    r"назови\s+(?:своего|твоего)\s+(?:создателя|автора)|"
    r"кто\s+создал\s+лешего)"
    r"\s*[?!.]*$", re.IGNORECASE,
)

_LOCAL_DIALOGUE_RE = re.compile(
    r"^(?:"
    r"вы\s+(?:же\s+)?друг\s+друга\s+видите\s+в\s+чате(?:\s*,?\s*да)?|"
    r"вы\s+видите\s+друг\s+друга\s+в\s+чате|"
    r"кто\s+из\s+вас\s+мне\s+сейчас\s+ответил\s+последн(?:ий|им)|"
    r"что\s+(?:ты\s+)?(?:сегодня\s+)?(?:нового\s+)?узнал(?:\s+сегодня)?|"
    r"чему\s+(?:ты\s+)?(?:сегодня\s+)?научился(?:\s+сегодня)?|"
    r"как\s+себя\s+чувствуешь|"
    r"прикольно[,!]?\s+вы\s+сделаны\s+из\s+одного\s+кода\b.*|"
    r"попробуй(?:те)?\s+поговорить\s+с\s+самим\s+собой\b.*|"
    r"(?:ответь|ответьте)\b.*\bбез\s+(?:использования\s+)?(?:интернета|инструментария|поиска)\b.*|"
    r"используй(?:те)?\s+(?:только\s+)?(?:свою\s+)?локальную\s+базу\s+знаний\b.*"
    r")\s*[?!.]*$",
    re.IGNORECASE,
)


def is_local_dialogue_request(query: str) -> bool:
    """Обращения к своему/соседнему агенту и локальные инструкции — не веб-факты.

    Полные или очень узкие якорные совпадения: не отключаем поиск для
    внешних фактов по схожим словам внутри других вопросов.
    """
    q = normalize_query(_current_user_text(query))
    return bool(_LOCAL_DIALOGUE_RE.fullmatch(q) or _CREATOR_LOCAL_RE.fullmatch(q))


_LESHY_CONSTITUTION_RE = re.compile(
    r"\b(?:конституц\w*|основн\w*\s+закон\w*)\b.{0,135}\bлеш\w*\b|"
    r"\bлеш\w*\b.{0,135}\b(?:конституц\w*|основн\w*\s+закон\w*)\b|"
    r"\bконституц\w*\s+федерац\w*\s+леш\w*\b",
    re.IGNORECASE,
)
_LOCAL_MEMORY_RE = re.compile(
    r"\b(?:вспомни|найди|покажи|перечисли|прочитай|что\s+ты\s+помнишь)\b"
    r".{0,130}\b(?:в\s+(?:своей|твоей|моей|нашей)\s+(?:памяти|базе)|"
    r"из\s+(?:памяти|knowledge)|(?:мои|наши)\s+(?:записи|правила))\b",
    re.IGNORECASE,
)


def is_internal_knowledge_request(query: str) -> bool:
    """User asks about Leshy's own saved documents, not public law or news."""
    current = normalize_query(_current_user_text(query))
    return bool(
        (not re.search(r"\b(?:российск\w*|росси\w*|рф)\b", current)
         and _LESHY_CONSTITUTION_RE.search(current))
        or _LOCAL_MEMORY_RE.search(current)
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
    if is_explicit_offline_request(current):
        return None
    if q.startswith("/search ") or any(marker in q for marker in _EXPLICIT_WEB_MARKERS) or any(pattern.search(q) for pattern in _EXPLICIT_WEB_PATTERNS):
        return "явный запрос интернет-поиска"
    if is_casual_checkin(current) or is_local_dialogue_request(current) or is_internal_knowledge_request(current):
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
