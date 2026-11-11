from __future__ import annotations

"""Текущие дата/время берутся из часов ОС, никогда из памяти LLM."""

from datetime import date, datetime
import re
from urllib.parse import urlsplit

_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)
_TIME_UNITS = r"(?:год|дата|день|число|месяц|время|час|день\s+недели|день,\s*месяц\s+и\s+год)"
_CURRENT = r"(?:сейчас|сегодня|ныне|в\s+настоящий\s+момент|на\s+данный\s+момент|текущ\w*)"
# Поддерживаются «какой сейчас год», «скажи текущую дату»,
# «выйди в интернет и посмотри какой сейчас день, месяц и год».
_ASK = re.compile(
    rf"\b(?:какой|какая|какое|который|скажи|назови|покажи|уточни|посмотри|проверь)\b"
    rf"[\s\S]{{0,160}}?\b{_CURRENT}\b[\s\S]{{0,100}}?\b{_TIME_UNITS}\b"
    rf"|\b{_CURRENT}\b[\s\S]{{0,100}}?\b{_TIME_UNITS}\b"
    rf"|\b(?:какая|скажи|назови|покажи|уточни)\b[\s\S]{{0,80}}?\b"
    rf"(?:сегодняшняя\s+дата|текущая\s+дата|сегодняшнее\s+число)\b",
    re.IGNORECASE,
)
# Вопросы о погоде, ценах, годе выпуска и событиях не перехватываем.
_NON_CLOCK = re.compile(r"\b(?:год[а-я]*\s+выпуска|погод[а-я]*|сколько\s+лет|какой\s+год\s+был)\b", re.IGNORECASE)
_TIME_QUESTION = re.compile(
    r"\b(?:который\s+(?:сейчас\s+)?час|сколько\s+(?:сейчас\s+)?времени|"
    r"сколько\s+время|какое\s+(?:сейчас\s+)?время|"
    r"сколько\s+на\s+часах|время\s+сейчас)\b",
    re.IGNORECASE,
)
_TODAY_QUESTION = re.compile(
    r"\b(?:какой\s+сегодня\s+(?:день|год|месяц)|"
    r"какое\s+сегодня\s+число|какая\s+сегодня\s+дата|"
    r"сегодня\s+(?:какой\s+)?(?:день\s+недели|месяц|число|дата))\b",
    re.IGNORECASE,
)


def current_clock_context() -> str:
    """Актуальная дата для КАЖДОЙ LLM-генерации, без запроса в интернет."""
    now = datetime.now().astimezone()
    zone = now.strftime("%z")
    if len(zone) == 5:
        zone = zone[:3] + ":" + zone[3:]
    return (
        f"Фактическая дата по системным часам этого компьютера: "
        f"{now.day} {_MONTHS[now.month-1]} {now.year} года "
        f"({_WEEKDAYS[now.weekday()]}), время {now.strftime('%H:%M:%S')}, UTC{zone}. "
        "Для вопросов о текущем дне/месяце/годе/времени используй эту дату, "
        "а не дату обучения модели. Не считай события из этого года невозможными "
        "лишь из-за своей устаревшей памяти. При этом дата компьютера НЕ "
        "подтверждает истинность новостей, существование CVE или других "
        "актуальных событий; для этого нужны проверенные внешние источники. "
        "Не объявляй информацию с веб-страницы выдумкой только потому, что "
        "её год новее границы знаний модели."
    )



def is_clock_request(message: str) -> bool:
    value = " ".join(str(message or "").strip().lower().split())
    if not value or _NON_CLOCK.search(value):
        return False
    return bool(_ASK.search(value) or _TIME_QUESTION.search(value) or _TODAY_QUESTION.search(value))


def clock_answer(message: str) -> str | None:
    if not is_clock_request(message):
        return None
    local = datetime.now().astimezone()
    tz = local.strftime("%z")
    if len(tz) == 5:
        tz = tz[:3] + ":" + tz[3:]
    return (
        f"Сейчас {local.day} {_MONTHS[local.month - 1]} {local.year} года, "
        f"{_WEEKDAYS[local.weekday()]}. "
        f"Местное время компьютера: {local.strftime('%H:%M:%S')} (UTC{tz}).\n"
        "Источник: системные часы компьютера. "
        "Это не результат интернет-поиска; если часы Windows неверны, нужно синхронизировать их."
    )


def checked_clock_answer(message: str, live_search=None) -> tuple[str | None, list[dict[str, str]]]:
    """На обычный вопрос ответ мгновенный; при ЯВНОЙ просьбе ищем и в сети.

    Дата всегда определяется ОС, но приложение честно отчитывается об
    отдельном интернет-поиске, не подменяя им локальные часы.
    """
    answer = clock_answer(message)
    if answer is None:
        return None, []
    from .research_policy import research_reason
    if research_reason(message) != "явный запрос интернет-поиска" or not callable(live_search):
        return answer, []
    try:
        result = live_search(
            "сегодняшняя дата и текущий год",
            limit=3, reason="явная проверка даты в интернете",
        )
        sources = [
            {"title": str(item.get("title") or ""), "url": str(item.get("url") or "")}
            for item in (result.get("results") or [])
            if isinstance(item, dict) and str(item.get("url") or "").startswith(("https://", "http://"))
        ]
        if sources:
            answer += (
                f"\nДополнительно интернет-поиск нашёл {len(sources)} источника(ов). "
                "Они показаны отдельно и не заменяют проверку системных часов."
            )
        else:
            answer += "\nИнтернет-поиск выполнен, но подходящих источников для независимой проверки часов нет."
        return answer, sources
    except Exception as exc:
        answer += (
            "\nИнтернет-поиск по Вашему запросу не удался ("
            + type(exc).__name__ + "). Я сообщил дату по локальным часам, "
            "не выдавая её за результат проверки в сети."
        )
        return answer, []



# Use the operating system clock for calculations; the language model's
# training date is never a reliable clock.
def current_date_anchor() -> str:
    today = datetime.now().astimezone().date()
    return (
        f"ТЕКУЩАЯ ДАТА ПО ЧАСАМ КОМПЬЮТЕРА: {today.isoformat()}. "
        "При ответе «сколько дней осталось» считай календарные дни от этой даты, "
        "не от даты обучения модели и не от дат из прошлых сообщений. "
        "Дата события должна подтверждаться источником."
    )


_RELEASE_TITLE_MARKERS = (
    "выйдет", "выходит", "дата выхода", "дата релиза", "релиз", "выпустят",
    "release", "launch", "comes out", "coming out", "arrives",
)
_RU_RELEASE_DATE = re.compile(
    r"\b([0-3]?\d)\s+("
    + "|".join(_MONTHS)
    + r")\s+(20\d{2})\b",
    flags=re.IGNORECASE,
)
_ISO_RELEASE_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DOTTED_RELEASE_DATE = re.compile(r"\b([0-3]?\d)\.(0?\d|1[0-2])\.(20\d{2})\b")


def _release_date_from_title(title: str) -> date | None:
    """Dates in search *titles* only: snippets often contain publication dates."""
    value = str(title or "").casefold().replace("ё", "е")
    if not any(marker in value for marker in _RELEASE_TITLE_MARKERS):
        return None
    matches: set[date] = set()
    for match in _RU_RELEASE_DATE.finditer(value):
        day, month, year = match.groups()
        try:
            matches.add(date(int(year), _MONTHS.index(month) + 1, int(day)))
        except (ValueError, IndexError):
            continue
    for match in _ISO_RELEASE_DATE.finditer(value):
        year, month, day = match.groups()
        try:
            matches.add(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    for match in _DOTTED_RELEASE_DATE.finditer(value):
        day, month, year = match.groups()
        try:
            matches.add(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return next(iter(matches)) if len(matches) == 1 else None


def _days_word(count: int) -> str:
    value = abs(count)
    if 11 <= value % 100 <= 14:
        return "дней"
    return "день" if value % 10 == 1 else "дня" if value % 10 in (2, 3, 4) else "дней"


def grounded_countdown_answer(
    question: str, sources: list[dict[str, str]], *, today: date | None = None,
) -> str | None:
    """A deterministic countdown only when web headlines give an unambiguous date.

    No result: leave the normal LLM flow untouched. Conflicting dates: do not
    invent a result. The sources have already passed the search relevance gate.
    """
    text = " ".join(str(question or "").casefold().split())
    if not re.search(r"(сколько\s+(?:еще\s+)?дн(?:ей|я|и)|дней\s+остал|через\s+сколько\s+дней|how\s+many\s+days|days\s+(?:left|until))", text):
        return None
    candidates: list[tuple[date, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        title = str(source.get("title") or "").strip()
        url = str(source.get("url") or "").strip()
        if not title or urlsplit(url).scheme not in ("http", "https"):
            continue
        release = _release_date_from_title(title)
        if release is not None:
            candidates.append((release, title))
    if not candidates or len({item[0] for item in candidates}) != 1:
        return None
    target, title = candidates[0]
    current = today or datetime.now().astimezone().date()
    delta = (target - current).days
    target_label = f"{target.day} {_MONTHS[target.month - 1]} {target.year} года"
    current_label = f"{current.day} {_MONTHS[current.month - 1]} {current.year} года"
    summary = title[:240]
    if delta >= 0:
        interval = f"До этой даты осталось {delta} {_days_word(delta)}."
    else:
        interval = f"Эта дата прошла {abs(delta)} {_days_word(delta)} назад."
    return (
        f"По найденному источнику: {summary}.\n\n"
        f"Дата события — {target_label}. Сегодня {current_label}. "
        f"{interval}"
    )


__all__ = ["clock_answer", "checked_clock_answer", "is_clock_request", "current_clock_context", "current_date_anchor", "grounded_countdown_answer"]
