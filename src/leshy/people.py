from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass
class InterfaceIdentity:
    interface: str
    external_id: str
    address: str = ""
    username: str = ""
    enabled: bool = True


@dataclass
class PersonProfile:
    person_id: str
    first_name: str = ""
    last_name: str = ""
    patronymic: str = ""
    preferred_name: str = ""
    language_code: str = ""
    birthday_day: int | None = None
    birthday_month: int | None = None
    birthday_year: int | None = None
    timezone: str = ""
    notes: str = ""
    birthday_greetings_enabled: bool = True
    last_birthday_greeting_year: int | None = None
    identities: list[InterfaceIdentity] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        parts = [self.last_name, self.first_name, self.patronymic]
        value = " ".join(part.strip() for part in parts if part and part.strip()).strip()
        return value or self.preferred_name or self.person_id

    @property
    def address_name(self) -> str:
        return self.preferred_name or self.first_name or self.full_name


class PeopleStore:
    """Single local person/profile store shared by every Leshy interface."""

    def __init__(self, brain_dir: Path):
        self.path = Path(brain_dir) / "People" / "people.json"
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

    @staticmethod
    def _decode(raw: dict[str, Any]) -> PersonProfile:
        value = dict(raw)
        value["identities"] = [InterfaceIdentity(**item) for item in value.get("identities", []) if isinstance(item, dict)]
        return PersonProfile(**value)

    def get(self, person_id: str) -> PersonProfile | None:
        raw = self._load_raw().get(str(person_id))
        return self._decode(raw) if isinstance(raw, dict) else None

    def all(self) -> list[PersonProfile]:
        people: list[PersonProfile] = []
        for raw in self._load_raw().values():
            if not isinstance(raw, dict):
                continue
            try:
                people.append(self._decode(raw))
            except (TypeError, ValueError):
                continue
        return people

    def find_by_identity(self, interface: str, external_id: str) -> PersonProfile | None:
        interface = str(interface).strip().lower()
        external_id = str(external_id)
        for person in self.all():
            if any(i.interface == interface and i.external_id == external_id for i in person.identities):
                return person
        return None

    def upsert_identity(
        self,
        interface: str,
        external_id: str,
        *,
        address: str = "",
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        language_code: str = "",
    ) -> PersonProfile:
        interface = str(interface).strip().lower()
        external_id = str(external_id)
        person = self.find_by_identity(interface, external_id)
        if person is None:
            person = PersonProfile(person_id=uuid4().hex)
            person.identities.append(InterfaceIdentity(interface=interface, external_id=external_id))
        identity = next(i for i in person.identities if i.interface == interface and i.external_id == external_id)
        identity.address = str(address or identity.address)
        identity.username = str(username or identity.username)
        if first_name:
            person.first_name = str(first_name)
        if last_name:
            person.last_name = str(last_name)
        if language_code:
            person.language_code = str(language_code)
        self.save(person)
        return person

    def link_identity(
        self,
        person_id: str,
        interface: str,
        external_id: str,
        *,
        address: str = "",
        username: str = "",
    ) -> PersonProfile:
        person = self.get(person_id)
        if person is None:
            raise KeyError(f"Unknown person: {person_id}")
        existing_owner = self.find_by_identity(interface, external_id)
        if existing_owner is not None and existing_owner.person_id != person_id:
            raise ValueError("Identity is already linked to another person")
        identity = next((i for i in person.identities if i.interface == interface and i.external_id == str(external_id)), None)
        if identity is None:
            identity = InterfaceIdentity(interface=str(interface).lower(), external_id=str(external_id))
            person.identities.append(identity)
        identity.address = str(address or identity.address)
        identity.username = str(username or identity.username)
        self.save(person)
        return person

    def update(self, person_id: str, **fields: Any) -> PersonProfile:
        person = self.get(person_id)
        if person is None:
            raise KeyError(f"Unknown person: {person_id}")
        allowed = {
            "first_name", "last_name", "patronymic", "preferred_name", "language_code",
            "birthday_day", "birthday_month", "birthday_year", "timezone", "notes",
            "birthday_greetings_enabled",
        }
        for key, value in fields.items():
            if key in allowed:
                setattr(person, key, value)
        self.save(person)
        return person

    def save(self, person: PersonProfile) -> None:
        raw = self._load_raw()
        raw[person.person_id] = asdict(person)
        self._save_raw(raw)

    def due_birthdays(self, now_utc: datetime | None = None) -> list[PersonProfile]:
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        due: list[PersonProfile] = []
        for person in self.all():
            if not person.birthday_greetings_enabled or not person.birthday_day or not person.birthday_month:
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

    def mark_birthday_greeted(self, person_id: str, year: int) -> None:
        person = self.get(person_id)
        if person is None:
            return
        person.last_birthday_greeting_year = int(year)
        self.save(person)

    def prompt_context(self, person_id: str) -> str:
        person = self.get(person_id)
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
