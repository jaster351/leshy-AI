from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .people import PeopleStore, PersonProfile


@dataclass(frozen=True)
class HolidayEvent:
    calendar_code: str
    day: date
    name: str

    @property
    def event_id(self) -> str:
        return f"{self.calendar_code}:{self.day.isoformat()}:{self.name}"


class HolidayProvider(Protocol):
    def events_on(self, calendar_code: str, day: date, language: str = "") -> list[HolidayEvent]: ...


class PythonHolidaysProvider:
    """Offline national/public holiday provider backed by the `holidays` package.

    Calendar codes use ISO 3166-1 alpha-2 country codes. Optional subdivision is
    written as `COUNTRY:SUBDIV`, for example `US:CA`.
    """

    def __init__(self):
        try:
            import holidays as holidays_package
        except ImportError as exc:
            raise RuntimeError(
                "National holiday support requires the offline 'holidays' package"
            ) from exc
        self._holidays = holidays_package

    def events_on(self, calendar_code: str, day: date, language: str = "") -> list[HolidayEvent]:
        code = str(calendar_code).strip().upper()
        if not code:
            return []
        country, separator, subdivision = code.partition(":")
        kwargs = {"years": [day.year]}
        if separator and subdivision:
            kwargs["subdiv"] = subdivision
        if language:
            kwargs["language"] = language
        try:
            calendar = self._holidays.country_holidays(country, **kwargs)
        except (KeyError, NotImplementedError, ValueError, TypeError):
            return []
        name = calendar.get(day)
        if not name:
            return []
        return [HolidayEvent(calendar_code=code, day=day, name=str(name))]


class NationalHolidayGreeter:
    """Generates and routes national holiday greetings for every Leshy interface."""

    def __init__(
        self,
        people: PeopleStore,
        provider: HolidayProvider,
        generate_text: Callable[[str, PersonProfile, list[HolidayEvent]], str],
        deliver: Callable[[PersonProfile, str], list[str]],
    ):
        self.people = people
        self.provider = provider
        self.generate_text = generate_text
        self.deliver = deliver

    @staticmethod
    def _local_date(person: PersonProfile, now_utc: datetime) -> date:
        local_now = now_utc
        if person.timezone:
            try:
                local_now = now_utc.astimezone(ZoneInfo(person.timezone))
            except ZoneInfoNotFoundError:
                pass
        return local_now.date()

    @staticmethod
    def prompt(person: PersonProfile, events: list[HolidayEvent]) -> str:
        holidays_text = "; ".join(
            f"{event.name} ({event.calendar_code})" for event in events
        )
        notes = f" Учитывай известные предпочтения/факты: {person.notes}." if person.notes else ""
        return (
            f"Сегодня у {person.full_name} национальный или государственный праздник: {holidays_text}. "
            "Составь от имени Лешего короткое, тёплое, естественное персональное поздравление. "
            "Учитывай культуру именно настроенных календарей, но не делай выводов о национальности, "
            "религии или происхождении человека сверх данных его профиля. "
            "Не используй канцелярские шаблоны и не упоминай, что текст создан ИИ."
            f"{notes}"
        )

    def tick(self, now_utc: datetime | None = None) -> dict[str, dict[str, list[str]]]:
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        result: dict[str, dict[str, list[str]]] = {}
        for person in self.people.all():
            if not person.holiday_greetings_enabled or not person.holiday_calendars:
                continue
            local_day = self._local_date(person, now_utc)
            events: list[HolidayEvent] = []
            seen: set[str] = set()
            for calendar_code in person.holiday_calendars:
                for event in self.provider.events_on(calendar_code, local_day, person.language_code):
                    if event.event_id in seen or self.people.was_holiday_greeted(person.person_id, event.event_id):
                        continue
                    seen.add(event.event_id)
                    events.append(event)
            if not events:
                continue
            text = str(self.generate_text(self.prompt(person, events), person, events) or "").strip()
            if not text:
                continue
            interfaces = list(dict.fromkeys(self.deliver(person, text) or []))
            if not interfaces:
                continue
            for event in events:
                self.people.mark_holiday_greeted(person.person_id, event.event_id)
            result[person.person_id] = {
                "interfaces": interfaces,
                "events": [event.name for event in events],
            }
        return result
