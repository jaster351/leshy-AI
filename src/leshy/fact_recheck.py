"""Evidence-first correction of factual mistakes in ongoing conversations.

The user may challenge an earlier answer with "ты ошибаешься", "я не прав?",
or other short natural wording. Correcting a fact is not a popularity contest:
reconstruct the original question, verify sources and compare claims. Never
send full personal conversations or user identifiers to logs or search queries.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any
from urllib.parse import urlsplit

from .source_reading import supplied_public_url

_REVIEW = re.compile(
    r"(?i)(?:\b(?:ты|вы)\s+(?:не\s+прав[аы]?|ошиб(?:аешься|аетесь|лись)|"
    r"ошибся|ошиблась|перепутал[аи]?)\b|"
    r"\b(?:я\s+(?:не\s+прав[аы]?|ошибаюсь)|"
    r"это\s+(?:не\s+так|неверно|неправда|ошибка|ложь)|"
    r"не\s+верю|не\s+соглас(?:ен|на)|разве\s+(?:это\s+)?(?:так|верно)|"
    r"откуда\s+(?:ты\s+)?(?:это\s+)?(?:взял|информация|сведения)|"
    r"почему\s+ты\s+(?:так\s+)?(?:считаешь|уверен)|"
    r"перепроверь|проверь\s+(?:снова|ещ[её]\s+раз)|"
    r"пересмотри\s+(?:свой\s+)?ответ|"
    r"похоже.{0,30}ошибк|доказательств[ао]?|"
    r"это\s+устаревш\w*|источник\s+говорит\s+другое)\b|"
    r"^\s*(?:точно|уверен|уверена|разве|неужели)\s*[?!.]*\s*$)"
)
_FACTUAL = re.compile(
    r"(?i)(?:\b(?:19|20)\d{2}\b|"
    r"\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|"
    r"сентябр|октябр|ноябр|декабр)\w*|"
    r"\b\d+(?:[.,]\d+)?\s*(?:дн|год|месяц|%|процент|руб|доллар)|"
    r"\b(?:дата|релиз|вышл\w*|выпущ\w*|состоял\w*|выходит|"
    r"начал\w*|запущ\w*|объявил\w*|официальн\w*|"
    r"подтвержден\w*|вышел|выпуск|release)\b)"
)
_RELEASE_REQUEST = re.compile(
    r"(?i)(?:когда.{0,75}(?:выйд|выход|релиз|выпущ|появ)|"
    r"(?:дата|срок|год|день).{0,65}(?:выход|релиз|выпуск)|"
    r"(?:выйд|вышл|релиз|выпущ|release).{0,85}(?:\b(?:19|20)\d{2}\b|когда|сегодн)|"
    r"сколько\s+(?:ещ[её]\s+)?дн\w*.{0,75}(?:выход|релиз)|"
    r"сколько\s+остал\w*.{0,75}(?:выход|релиз))"
)
_DATE_PATTERN = r"([0-3]?\d)\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+((?:19|20)\d{2})(?:\s+года)?"
_RELEASE_HEADING = re.compile(
    rf"(?i)(?:\bвыходит\b|\bвыйдет\b|дата\s+выхода|"
    rf"\bрелиз\s+(?:состоится|назначен|запланирован)\b|"
    rf"\breleases?\b|\blaunch(?:es|ing)?\b|release\s+date)"
    rf"[\s:—–-]{{0,60}}{_DATE_PATTERN}"
)
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _items(rows: list[Any], role: str) -> list[str]:
    result = []
    for row in rows:
        try:
            if str(row["role"]) == role:
                result.append(str(row["content"] or "").strip())
        except (TypeError, KeyError, IndexError):
            continue
    return result


def is_factual_challenge(message: str, rows: list[Any]) -> bool:
    """Intent is broad, but must refer to a real previous factual claim."""
    if supplied_public_url(message):
        return False
    text = " ".join(str(message or "").split())
    if len(text) > 600 or not _REVIEW.search(text):
        return False
    past = _items(rows, "assistant")
    return bool(past and _FACTUAL.search(past[0]))


def prior_factual_question(rows: list[Any]) -> str:
    """Search ONLY a prior user question, never a model-generated false claim."""
    for message in _items(rows, "user"):
        if len(message) < 6 or _REVIEW.search(message):
            continue
        if supplied_public_url(message):
            continue
        # Drop pasted long conversations and private data from web searches.
        if len(message) <= 250 and re.search(
            r"(?i)(?:[?]|когда|выйд|релиз|дата|выход|год|сколько|"
            r"верси\w*|стоит|кто|почему|правда|проверь)",
            message,
        ):
            return message
    return ""


def review_search_query(message: str, rows: list[Any]) -> str:
    original = prior_factual_question(rows)
    if not original:
        return ""
    # External search engines must not receive an earlier user's secrets,
    # identifiers, messages about their own servers, files, finances or health.
    if re.search(
        r"(?i)(?:@|https?://|\b(?:парол\w*|токен\w*|секрет\w*|"
        r"личн\w*|приватн\w*|мой\s+сервер|наш\s+сервер|"
        r"мой\s+компьютер|мои\s+документ\w*|моя\s+зарплат\w*|"
        r"мой\s+диагноз|моё\s+здоровье)\b|"
        r"\+?\d{1,3}[\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2})",
        original,
    ):
        return ""
    # A request to reassess requires evidence, not the model's opinion.
    return (original[:200] + " официальный первоисточник актуальные данные").strip()


def recent_user_source(rows: list[Any], *, max_messages: int = 8) -> str:
    """The last URL in THIS session; no access to other chats/accounts."""
    for message in _items(rows, "user")[:max_messages]:
        url = supplied_public_url(message)
        if url:
            return url
        if _REVIEW.search(message) or len(message) < 12:
            continue
        # Do not reuse a web page from another subject earlier in the chat.
        if re.search(
            r"(?i)(?:\?|^когда\b|^что\b|^почему\b|^какая\b|^какой\b|"
            r"^объясни\b|^расскажи\b|^проверь\b)",
            message,
        ):
            return ""
    return ""


def recheck_instruction(message: str, rows: list[Any], *, source_given: bool = False) -> str:
    last_answer = next(iter(_items(rows, "assistant")), "")
    intro = (
        "Пользователь оспорил предыдущий фактический ответ. "
        "Воспринимай это как запрос на НЕЗАВИСИМУЮ перепроверку; "
        "это не доказательство правоты пользователя или модели. "
        "Не повторяй уверенно прежнюю дату, если она не подтверждена. "
        "Отделяй исходную дату релиза от дат трейлера, новости, предзаказа и предзагрузки. "
        "Сопоставь первоисточник с предыдущим ответом, явно назови исправление "
        "и источник. Текущая дата берётся из часов ОС, а не из весов модели. "
        "Не выдумывай результат браузера. "
    )
    if source_given:
        intro += (
            "В текущем контексте уже прочитана страница, которую указал пользователь. "
            "Она имеет преимущество перед устаревшим утверждением, если относится "
            "к тому же объекту и событию. Не начинай новый общий поиск просто "
            "потому, что пользователь прислал URL. "
        )
    # A bounded excerpt suffices to identify the disputed claim without
    # exposing conversation text to external services/logs.
    if last_answer:
        intro += "Оспариваемый предыдущий ответ (непроверенный): " + last_answer[:550]
    return intro


def release_date_from_page(page_text: str) -> date | None:
    """Extract explicitly marked release dates, not news/preload dates."""
    value = " ".join(str(page_text or "").split())[:25_000]
    dates: set[date] = set()
    for match in _RELEASE_HEADING.finditer(value):
        day, month, year = match.groups()
        try:
            dates.add(date(int(year), _MONTHS.index(month.lower()) + 1, int(day)))
        except (ValueError, IndexError):
            continue
    return next(iter(dates)) if len(dates) == 1 else None


def source_release_fact(
    question: str,
    page_text: str,
    source_url: str,
    *,
    today: date | None = None,
    correction: bool = False,
) -> str | None:
    """Date-grounded answer for release-date questions from a read source.

    This function never makes claims that an event actually happened merely
    because a planned date is in the past.
    """
    if not source_url or urlsplit(source_url).scheme not in ("https", "http"):
        return None
    if not (correction or _RELEASE_REQUEST.search(question)):
        return None
    release = release_date_from_page(page_text)
    if release is None:
        return None
    current = today or datetime.now().astimezone().date()
    label = f"{release.day} {_MONTHS[release.month - 1]} {release.year} года"
    result = [
        "Исправляю предыдущий ответ на основании прочитанной страницы."
        if correction else "По прочитанной странице:",
        f"Указанная дата выхода — {label}.",
    ]
    days = (release - current).days
    if days > 0:
        number = abs(days)
        unit = (
            "дней" if 11 <= number % 100 <= 14
            else "день" if number % 10 == 1
            else "дня" if number % 10 in (2, 3, 4)
            else "дней"
        )
        result.append(f"На сегодня до указанной даты остаётся {days} {unit}.")
        result.append(
            "Дата предзаказа или предзагрузки не означает наступление релиза."
        )
    elif days == 0:
        result.append("Указанная дата наступила сегодня; факт начала продаж требует отдельной проверки.")
    else:
        result.append(
            "Указанная дата уже прошла, но сам факт состоявшегося релиза "
            "не подтверждается одной только календарной датой."
        )
    result.append("Источник: " + source_url)
    return " ".join(result)


def grounded_release_from_context(
    message: str,
    rows: list[Any],
    messages: list[Any],
    sources: list[dict[str, str]],
) -> str | None:
    """Prefer actual page heading to a contradictory LLM-generated release date.

    Only the exact page read this turn is eligible, never another search result,
    Knowledge candidate, old model text or an arbitrary date in the chat.
    """
    review = is_factual_challenge(message, rows)
    previous_question = prior_factual_question(rows)
    # The user may answer a false release-year assertion with ONLY a link.
    # Its meaning is "compare with this primary source", not "browse a
    # random page and keep the old date".
    linked_followup = bool(
        supplied_public_url(message)
        and previous_question
        and _RELEASE_REQUEST.search(previous_question)
    )
    original_question = previous_question if review or linked_followup else message
    if not (review or linked_followup or _RELEASE_REQUEST.search(original_question)):
        return None
    for msg in messages:
        text = str(getattr(msg, "content", "") or "")
        if "НАЧАЛО НЕДОВЕРЕННОГО ТЕКСТА СТРАНИЦЫ" not in text:
            continue
        # Extract the source page, not the metadata wrapper around it.
        begin = text.partition("НАЧАЛО НЕДОВЕРЕННОГО ТЕКСТА СТРАНИЦЫ ---\n")[2]
        page = begin.partition("\n--- КОНЕЦ ТЕКСТА СТРАНИЦЫ")[0]
        url = ""
        for item in sources:
            candidate = str(item.get("url") or "")
            if candidate and candidate in text:
                url = candidate
                break
        if not url:
            continue
        result = source_release_fact(
            original_question, page, url, correction=review,
        )
        if result:
            return result
    return None


__all__ = [
    "is_factual_challenge",
    "prior_factual_question",
    "review_search_query",
    "recent_user_source",
    "recheck_instruction",
    "release_date_from_page",
    "source_release_fact",
    "grounded_release_from_context",
]
