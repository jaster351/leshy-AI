from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .people import PeopleStore, PersonProfile


class BirthdayGreeter:
    """Generates one greeting and routes it through every enabled Leshy interface."""

    def __init__(
        self,
        people: PeopleStore,
        generate_text: Callable[[str, PersonProfile], str],
        deliver: Callable[[PersonProfile, str], list[str]],
    ):
        self.people = people
        self.generate_text = generate_text
        self.deliver = deliver

    @staticmethod
    def prompt(person: PersonProfile) -> str:
        age_hint = ""
        if person.birthday_year:
            age_hint = (
                " Год рождения известен, но не акцентируй возраст без необходимости "
                "и не делай чувствительных выводов о человеке."
            )
        notes = f" Учитывай известные предпочтения/факты: {person.notes}." if person.notes else ""
        return (
            f"Сегодня день рождения у {person.full_name}. "
            "Составь от имени Лешего короткое, тёплое, живое и персональное поздравление. "
            "Не используй шаблонные канцелярские фразы и не упоминай, что текст создан ИИ."
            f"{age_hint}{notes}"
        )

    def tick(self, now_utc: datetime | None = None) -> dict[str, list[str]]:
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        delivered: dict[str, list[str]] = {}
        for person in self.people.due_birthdays(now_utc):
            text = str(self.generate_text(self.prompt(person), person) or "").strip()
            if not text:
                continue
            interfaces = list(dict.fromkeys(self.deliver(person, text) or []))
            if not interfaces:
                continue
            local_year = now_utc.year
            if person.timezone:
                try:
                    from zoneinfo import ZoneInfo
                    local_year = now_utc.astimezone(ZoneInfo(person.timezone)).year
                except Exception:
                    pass
            self.people.mark_birthday_greeted(person.person_id, local_year)
            delivered[person.person_id] = interfaces
        return delivered


def delivery_targets(person: PersonProfile) -> list[tuple[str, str, str]]:
    """Return enabled (interface, external_id, address) routes for adapters.

    Telegram may send immediately, Windows/macOS/Linux desktop may raise a local
    notification, Web may enqueue a greeting for the next active session, and API
    may expose the event to a subscribed client. All routes refer to one profile.
    """
    return [
        (identity.interface, identity.external_id, identity.address)
        for identity in person.identities
        if identity.enabled
    ]
