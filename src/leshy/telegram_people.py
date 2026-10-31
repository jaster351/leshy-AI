from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass
class TelegramPerson:
    user_id: int
    chat_id: int
    first_name: str = ""
    last_name: str = ""
    patronymic: str = ""
    username: str = ""
    preferred_name: str = ""
    language_code: str = ""
    birthday_day: int | None = None
    birthday_month: int | None = None
    birthday_year: int | None = None
    timezone: str = ""
    notes: str = ""
    last_birthday_greeting_year: int | None = None

    @property
    def full_name(self) -> str:
        parts = [self.last_name, self.first_name, self.patronymic]
        value = " ".join(part.strip() for part in parts if part and part.strip()).strip()
        return value or self.preferred_name or self.username or str(self.user_id)

    @property
    def address_name(self) -> str:
        return self.preferred_name or self.first_name or self.full_name


class TelegramPeopleStore:
    """Private local profile store for people who talk to Leshy through Telegram."""

    def __init__(self, brain_dir: Path):
        self.path = Path(brain_dir) / "Telegram" / "people.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_raw(self, value: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="people.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def get(self, user_id: int) -> TelegramPerson | None:
        raw = self._load_raw().get(str(int(user_id)))
        return TelegramPerson(**raw) if isinstance(raw, dict) else None

    def all(self) -> list[TelegramPerson]:
        people: list[TelegramPerson] = []
        for raw in self._load_raw().values():
            if not isinstance(raw, dict):
                continue
            try:
                people.append(TelegramPerson(**raw))
            except (TypeError, ValueError):
                continue
        return people

    def upsert_from_message(self, message: dict[str, Any]) -> TelegramPerson | None:
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        try:
            user_id = int(sender.get("id"))
            chat_id = int(chat.get("id"))
        except (TypeError, ValueError):
            return None

        current = self.get(user_id) or TelegramPerson(user_id=user_id, chat_id=chat_id)
        current.chat_id = chat_id
        current.first_name = str(sender.get("first_name") or current.first_name or "")
        current.last_name = str(sender.get("last_name") or current.last_name or "")
        current.username = str(sender.get("username") or current.username or "")
        current.language_code = str(sender.get("language_code") or current.language_code or "")

        birthdate = chat.get("birthdate") or {}
        if isinstance(birthdate, dict):
            try:
                if birthdate.get("day") is not None:
                    current.birthday_day = int(birthdate["day"])
                if birthdate.get("month") is not None:
                    current.birthday_month = int(birthdate["month"])
                if birthdate.get("year") is not None:
                    current.birthday_year = int(birthdate["year"])
            except (TypeError, ValueError):
                pass
        self.save(current)
        return current

    def update_manual(self, user_id: int, **fields: Any) -> TelegramPerson:
        current = self.get(user_id)
        if current is None:
            raise KeyError(f"Unknown Telegram user: {user_id}")
        allowed = {
            "patronymic", "preferred_name", "birthday_day", "birthday_month",
            "birthday_year", "timezone", "notes"
        }
        for key, value in fields.items():
            if key in allowed:
                setattr(current, key, value)
        self.save(current)
        return current

    def save(self, person: TelegramPerson) -> None:
        raw = self._load_raw()
        raw[str(person.user_id)] = asdict(person)
        self._save_raw(raw)

    def due_birthdays(self, now_utc: datetime | None = None) -> list[TelegramPerson]:
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        due: list[TelegramPerson] = []
        for person in self.all():
            if not person.birthday_day or not person.birthday_month or not person.chat_id:
                continue
            local_now = now_utc
            if person.timezone:
                try:
                    local_now = now_utc.astimezone(ZoneInfo(person.timezone))
                except ZoneInfoNotFoundError:
                    pass
            if (
                local_now.day == person.birthday_day
                and local_now.month == person.birthday_month
                and person.last_birthday_greeting_year != local_now.year
            ):
                due.append(person)
        return due

    def mark_birthday_greeted(self, user_id: int, year: int) -> None:
        person = self.get(user_id)
        if person is None:
            return
        person.last_birthday_greeting_year = int(year)
        self.save(person)

    def prompt_context(self, user_id: int) -> str:
        person = self.get(user_id)
        if person is None:
            return ""
        fields = [f"Имя собеседника: {person.full_name}."]
        if person.preferred_name:
            fields.append(f"Предпочтительное обращение: {person.preferred_name}.")
        if person.birthday_day and person.birthday_month:
            birthday = f"{person.birthday_day:02d}.{person.birthday_month:02d}"
            if person.birthday_year:
                birthday += f".{person.birthday_year}"
            fields.append(f"День рождения: {birthday}.")
        if person.notes:
            fields.append(f"Заметки: {person.notes}")
        return " ".join(fields)
