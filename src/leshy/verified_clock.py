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


def is_clock_request(message: str) -> bool:
    value = " ".join(str(message or "").strip().lower().split())
    if not value or _NON_CLOCK.search(value):
        return False
    return bool(_ASK.search(value))


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


__all__ = ["clock_answer", "is_clock_request"]
