from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .telegram_people import TelegramPeopleStore, TelegramPerson


class TelegramBirthdayGreeter:
    """Generates and sends one personal birthday greeting per local calendar year."""

    def __init__(
        self,
        people: TelegramPeopleStore,
        generate_text: Callable[[str, TelegramPerson], str],
        send_message: Callable[[int, str], None],
    ):
        self.people = people
        self.generate_text = generate_text
        self.send_message = send_message

    @staticmethod
    def _prompt(person: TelegramPerson) -> str:
        age_hint = ""
        if person.birthday_year:
            age_hint = (
                " Год рождения известен, но не упоминай возраст без необходимости "
                "и не делай чувствительных выводов о человеке."
            )
        notes = f" Учитывай эти известные предпочтения/факты: {person.notes}." if person.notes else ""
        return (
            f"Сегодня день рождения у {person.full_name}. "
            f"Составь от имени Лешего короткое, тёплое, живое и персональное поздравление на русском языке. "
            f"Не используй шаблонные канцелярские фразы и не упоминай, что текст сгенерирован ИИ.{age_hint}{notes}"
        )

    def tick(self, now_utc: datetime | None = None) -> list[int]:
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        sent: list[int] = []
        for person in self.people.due_birthdays(now_utc):
            text = str(self.generate_text(self._prompt(person), person) or "").strip()
            if not text:
                continue
            self.send_message(person.chat_id, text)
            local_year = now_utc.year
            if person.timezone:
                try:
                    from zoneinfo import ZoneInfo
                    local_year = now_utc.astimezone(ZoneInfo(person.timezone)).year
                except Exception:
                    pass
            self.people.mark_birthday_greeted(person.user_id, local_year)
            sent.append(person.user_id)
        return sent
