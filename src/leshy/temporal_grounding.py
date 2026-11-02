from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re


_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_RU_WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)
_DYNAMIC_MARKERS = (
    "погод", "weather", "сегодня", "today", "сейчас", "now", "завтра", "tomorrow",
    "вчера", "yesterday", "курс", "цена", "стоимость", "новост", "расписан", "статус сервиса",
)


@dataclass(frozen=True)
class TemporalGrounding:
    now: datetime
    target_date: datetime
    relation: str

    @property
    def iso_date(self) -> str:
        return self.target_date.date().isoformat()

    @property
    def russian_date(self) -> str:
        value = self.target_date
        return f"{value.day} {_RU_MONTHS[value.month - 1]} {value.year}"


def local_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.astimezone()
    return value


def temporal_grounding(message: str, *, now: datetime | None = None) -> TemporalGrounding | None:
    text = " ".join(str(message or "").lower().replace("ё", "е").split())
    if not text or not any(marker in text for marker in _DYNAMIC_MARKERS):
        return None
    current = local_now(now)
    relation = "current"
    target = current
    if re.search(r"\b(завтра|tomorrow)\b", text):
        relation = "tomorrow"
        target = current + timedelta(days=1)
    elif re.search(r"\b(вчера|yesterday)\b", text):
        relation = "yesterday"
        target = current - timedelta(days=1)
    elif re.search(r"\b(сегодня|today)\b", text) or "погод" in text or "weather" in text:
        relation = "today"
        target = current
    return TemporalGrounding(now=current, target_date=target, relation=relation)


def temporal_system_instruction(message: str, *, now: datetime | None = None) -> str:
    ground = temporal_grounding(message, now=now)
    if ground is None:
        return ""
    current = ground.now
    offset = current.strftime("%z")
    if len(offset) == 5:
        offset = offset[:3] + ":" + offset[3:]
    weekday = _RU_WEEKDAYS[ground.target_date.weekday()]
    return (
        "Временной контекст приложения: текущая локальная дата и время сервера — "
        f"{current.date().isoformat()} {current.strftime('%H:%M:%S')} UTC{offset}. "
        f"Запрошенная относительная дата интерпретирована как {ground.iso_date} "
        f"({ground.russian_date}, {weekday}). Для погоды, цен, курсов, новостей, расписаний и других "
        "динамических данных сначала опирайся на эту дату, затем на результаты свежего интернет-поиска. "
        "Не выдавай старый прогноз или старые данные как текущие."
    )


def ground_search_query(query: str, *, now: datetime | None = None) -> str:
    value = " ".join(str(query or "").split()).strip()
    ground = temporal_grounding(value, now=now)
    if ground is None:
        return value
    # Явная дата делает поисковую выдачу устойчивой к неверной интерпретации
    # «сегодня/завтра» поисковиком, локальной моделью и кешами источников.
    suffix = f"{ground.iso_date} {ground.russian_date}"
    if ground.iso_date in value:
        return value
    return f"{value} {suffix}".strip()


__all__ = [
    "TemporalGrounding",
    "ground_search_query",
    "local_now",
    "temporal_grounding",
    "temporal_system_instruction",
]
