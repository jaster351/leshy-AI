from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from leshy.telegram_birthdays import TelegramBirthdayGreeter
from leshy.telegram_people import TelegramPeopleStore


class TelegramPeopleTests(unittest.TestCase):
    def test_profile_is_enriched_from_telegram_and_manual_fields_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelegramPeopleStore(Path(tmp) / "LeshyBrain")
            person = store.upsert_from_message({
                "from": {
                    "id": 101,
                    "first_name": "Иван",
                    "last_name": "Петров",
                    "username": "ivan_petrov",
                    "language_code": "ru",
                },
                "chat": {
                    "id": 101,
                    "birthdate": {"day": 5, "month": 11, "year": 1990},
                },
            })
            self.assertIsNotNone(person)
            store.update_manual(101, patronymic="Сергеевич", preferred_name="Иван", timezone="Asia/Yekaterinburg")
            stored = store.get(101)
            self.assertEqual(stored.full_name, "Петров Иван Сергеевич")
            self.assertEqual(stored.birthday_day, 5)
            self.assertEqual(stored.birthday_month, 11)
            self.assertEqual(stored.timezone, "Asia/Yekaterinburg")

    def test_birthday_greeting_is_generated_and_sent_only_once_per_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TelegramPeopleStore(Path(tmp) / "LeshyBrain")
            store.upsert_from_message({
                "from": {"id": 7, "first_name": "Анна"},
                "chat": {"id": 700},
            })
            store.update_manual(7, birthday_day=31, birthday_month=10, timezone="Asia/Yekaterinburg")
            generated = []
            sent = []

            def generate(prompt, person):
                generated.append((prompt, person.user_id))
                return "Анна, с днём рождения! Пусть день будет тёплым и радостным."

            greeter = TelegramBirthdayGreeter(store, generate, lambda chat_id, text: sent.append((chat_id, text)))
            now = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)
            self.assertEqual(greeter.tick(now), [7])
            self.assertEqual(greeter.tick(now), [])
            self.assertEqual(len(generated), 1)
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][0], 700)


if __name__ == "__main__":
    unittest.main()
