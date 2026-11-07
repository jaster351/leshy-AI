from __future__ import annotations

"""Текущие дата/время берутся из часов ОС, никогда из памяти LLM."""

from datetime import datetime
import re

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


__all__ = ["clock_answer", "checked_clock_answer", "is_clock_request", "current_clock_context"]
