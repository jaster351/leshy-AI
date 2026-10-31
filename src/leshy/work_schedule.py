from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


MOSCOW_TIMEZONE = "Europe/Moscow"


@dataclass(frozen=True, slots=True)
class WorkSchedule:
    """Расписание рабочего ассистирования по московскому времени."""

    timezone_name: str = MOSCOW_TIMEZONE
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5)  # Понедельник–суббота.
    start: time = time(11, 0)
    end: time = time(21, 0)

    def local_time(self, now: datetime | None = None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo(self.timezone_name))

    def contains(self, now: datetime | None = None) -> bool:
        local = self.local_time(now)
        if local.weekday() not in self.weekdays:
            return False
        current = local.timetz().replace(tzinfo=None)
        return self.start <= current < self.end


@dataclass(frozen=True, slots=True)
class WorkAssistancePolicy:
    """Разрешает рабочие уведомления только во время смены и активной рабочей сессии."""

    schedule: WorkSchedule = field(default_factory=WorkSchedule)
    require_presence: bool = True

    def allows(
        self,
        *,
        now: datetime | None = None,
        workstation_active: bool = False,
        manual_work_mode: bool = False,
    ) -> bool:
        if not self.schedule.contains(now):
            return False
        if not self.require_presence:
            return True
        return bool(workstation_active or manual_work_mode)


def parse_event_time(value: object) -> datetime | None:
    """Разобрать ISO-время события; при ошибке использовать текущее время вызывающей стороны."""

    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def truthy(value: object) -> bool:
    """Нормализовать булевы признаки из метаданных каналов."""

    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on", "да", "включено"}
