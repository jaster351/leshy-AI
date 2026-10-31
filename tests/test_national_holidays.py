from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from leshy.national_holidays import HolidayEvent, NationalHolidayGreeter
from leshy.people import PeopleStore


class FakeHolidayProvider:
    def __init__(self):
        self.calls = []

    def events_on(self, calendar_code, day, language=""):
        self.calls.append((calendar_code, day, language))
        if calendar_code == "RU" and day == date(2026, 11, 4):
            return [HolidayEvent("RU", day, "День народного единства")]
        if calendar_code == "KZ" and day == date(2026, 11, 4):
            return [HolidayEvent("KZ", day, "Тестовый национальный праздник")]
        return []


class NationalHolidayTests(unittest.TestCase):
    def test_language_does_not_infer_national_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            people = PeopleStore(Path(tmp) / "LeshyBrain")
            person = people.upsert_identity(
                "web", "session-1", first_name="Иван", language_code="ru"
            )
            generated = []
            provider = FakeHolidayProvider()
            greeter = NationalHolidayGreeter(
                people,
                provider,
                lambda prompt, profile, events: generated.append(prompt) or "Поздравляю!",
                lambda profile, text: ["web"],
            )
            result = greeter.tick(datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc))
            self.assertEqual(result, {})
            self.assertEqual(provider.calls, [])
            self.assertEqual(generated, [])
            self.assertEqual(person.holiday_calendars, [])

    def test_configured_holiday_is_delivered_through_all_linked_interfaces_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            people = PeopleStore(Path(tmp) / "LeshyBrain")
            person = people.upsert_identity(
                "web", "session-1", first_name="Иван", language_code="ru"
            )
            people.link_identity(person.person_id, "telegram", "100", address="100")
            people.update(
                person.person_id,
                holiday_calendars=["RU"],
                timezone="UTC",
            )
            provider = FakeHolidayProvider()
            generated = []
            delivered = []

            def generate(prompt, profile, events):
                generated.append((prompt, profile.person_id, [event.name for event in events]))
                return "Иван, с Днём народного единства!"

            def deliver(profile, text):
                delivered.append((profile.person_id, text))
                return [identity.interface for identity in profile.identities if identity.enabled]

            greeter = NationalHolidayGreeter(people, provider, generate, deliver)
            now = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
            first = greeter.tick(now)
            second = greeter.tick(now)

            self.assertEqual(set(first[person.person_id]["interfaces"]), {"web", "telegram"})
            self.assertEqual(first[person.person_id]["events"], ["День народного единства"])
            self.assertEqual(second, {})
            self.assertEqual(len(generated), 1)
            self.assertEqual(len(delivered), 1)

    def test_multiple_explicit_calendars_can_be_used_for_one_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            people = PeopleStore(Path(tmp) / "LeshyBrain")
            person = people.upsert_identity("api", "user-1", first_name="Алекс")
            people.update(person.person_id, holiday_calendars=["RU", "KZ"], timezone="UTC")
            provider = FakeHolidayProvider()
            seen_events = []

            greeter = NationalHolidayGreeter(
                people,
                provider,
                lambda prompt, profile, events: seen_events.extend(event.name for event in events) or "Поздравляю!",
                lambda profile, text: ["api"],
            )
            greeter.tick(datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc))
            self.assertEqual(
                seen_events,
                ["День народного единства", "Тестовый национальный праздник"],
            )

    def test_person_can_disable_holiday_greetings(self):
        with tempfile.TemporaryDirectory() as tmp:
            people = PeopleStore(Path(tmp) / "LeshyBrain")
            person = people.upsert_identity("windows", "local-user", first_name="Анна")
            people.update(
                person.person_id,
                holiday_calendars=["RU"],
                holiday_greetings_enabled=False,
                timezone="UTC",
            )
            provider = FakeHolidayProvider()
            greeter = NationalHolidayGreeter(
                people,
                provider,
                lambda prompt, profile, events: "Поздравляю!",
                lambda profile, text: ["windows"],
            )
            self.assertEqual(
                greeter.tick(datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)),
                {},
            )
            self.assertEqual(provider.calls, [])


if __name__ == "__main__":
    unittest.main()
