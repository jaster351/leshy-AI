from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.browser_skills import BrowserSkillExecution
from leshy.parcel_tracking import (
    GDEPOSYLKA_SKILL,
    ParcelTrackingService,
    extract_tracking_numbers,
    parse_parcel_intent,
    parse_parcel_page,
)
from leshy.storage import Storage, utcnow


TRACK = "RR123456789CN"


class FakeRunner:
    def __init__(self):
        self.calls: list[str] = []
        self.text = (
            "Отслеживание почтового отправления\n"
            f"{TRACK}\n"
            "Почта Китая\n"
            "Прибыло в сортировочный центр\n"
            "Челябинск\n"
            "02 ноября 2026 13:40\n"
        )

    def run_form_skill(self, skill, value, *, activity=None):
        self.calls.append(value)
        if activity:
            activity("browser_skill_opening", {"status": "Открываю сайт", "skill_id": skill.skill_id})
        return BrowserSkillExecution(
            skill_id=skill.skill_id,
            status="success",
            page_url="https://gdeposylka.ru/track/" + value,
            page_title="Отслеживание посылки",
            visible_text=self.text.replace(TRACK, value),
            input_value=value,
            browser_name="Edge",
            elapsed_ms=100,
        )


class FakeTelegram:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    def _send(self, chat_id: int, text: str):
        self.messages.append((chat_id, text))


class FakeApp:
    def __init__(self, root: Path):
        self.storage = Storage(root / "leshy.db")
        self.telegram = FakeTelegram()

    def _save_assistant(self, sid: str, answer: str) -> str:
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (sid, "assistant", answer, now),
            )
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
        return now


class ParcelTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = FakeApp(self.root)
        self.runner = FakeRunner()
        self.service = ParcelTrackingService(self.app, runner=self.runner)
        now = utcnow()
        with self.app.storage.connection() as conn:
            conn.execute(
                "INSERT INTO sessions(id,owner_id,title,title_source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("web-session", "web:local", "Посылка", "user", now, now),
            )

    def tearDown(self) -> None:
        self.service.stop()
        self.tmp.cleanup()

    def test_track_detection_and_natural_intents(self) -> None:
        self.assertEqual(extract_tracking_numbers(TRACK), [TRACK])
        self.assertEqual(parse_parcel_intent(TRACK).action, "track")
        self.assertEqual(parse_parcel_intent(f"Где моя посылка {TRACK}?").action, "track")
        self.assertEqual(parse_parcel_intent(f"Следи за {TRACK} и сообщай об изменениях").action, "follow")
        self.assertEqual(parse_parcel_intent(f"Перестань следить за {TRACK}").action, "stop")
        self.assertEqual(parse_parcel_intent("Покажи мои посылки").action, "list")
        self.assertIsNone(parse_parcel_intent("Расскажи про Челябинск"))

    def test_gdeposylka_skill_uses_human_visible_form_hints(self) -> None:
        self.assertEqual(GDEPOSYLKA_SKILL.start_url, "https://gdeposylka.ru/")
        self.assertIn("Номер отслеживания", GDEPOSYLKA_SKILL.input_hints)
        self.assertIn("Отследить посылку", GDEPOSYLKA_SKILL.submit_hints)

    def test_page_parser_extracts_stable_status(self) -> None:
        parsed = parse_parcel_page(TRACK, self.runner.text, "https://gdeposylka.ru/track/test", browser_name="Edge")
        self.assertEqual(parsed.tracking_number, TRACK)
        self.assertEqual(parsed.status_category, "in_transit")
        self.assertIn("сортировочный центр", parsed.status.lower())
        self.assertTrue(parsed.fingerprint)
        self.assertEqual(parsed.browser_name, "Edge")

    def test_follow_persists_subscription_and_does_not_need_llm(self) -> None:
        result = self.service.follow(
            "web:local",
            TRACK,
            notify_channel="web",
            notify_target="web:local",
            session_id="web-session",
        )
        self.assertEqual(result.status_category, "in_transit")
        items = self.service.list("web:local")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["tracking_number"], TRACK)
        self.assertEqual(items[0]["state"], "active")
        self.assertEqual(self.runner.calls, [TRACK])

    def test_status_change_creates_one_telegram_notification(self) -> None:
        self.service.follow(
            "telegram:123",
            TRACK,
            notify_channel="telegram",
            notify_target="555",
            session_id="telegram:555:123",
        )
        self.runner.text = (
            f"{TRACK}\n"
            "Посылка доставлена получателю\n"
            "Челябинск\n"
            "02 ноября 2026 18:00\n"
        )
        with self.app.storage.connection() as conn:
            row = conn.execute("SELECT * FROM parcel_subscriptions WHERE owner_id='telegram:123'").fetchone()
        self.service._check_row(row)
        self.service.deliver_pending()
        self.assertEqual(len(self.app.telegram.messages), 1)
        self.assertEqual(self.app.telegram.messages[0][0], 555)
        self.assertIn("обновилась", self.app.telegram.messages[0][1])
        with self.app.storage.connection() as conn:
            state = conn.execute("SELECT state FROM parcel_subscriptions WHERE owner_id='telegram:123'").fetchone()["state"]
        self.assertEqual(state, "delivered")
        self.service.deliver_pending()
        self.assertEqual(len(self.app.telegram.messages), 1)

    def test_web_notification_is_written_into_original_chat(self) -> None:
        self.service.follow(
            "web:local",
            TRACK,
            notify_channel="web",
            notify_target="web:local",
            session_id="web-session",
        )
        self.runner.text = f"{TRACK}\nПосылка доставлена получателю\n"
        with self.app.storage.connection() as conn:
            row = conn.execute("SELECT * FROM parcel_subscriptions WHERE owner_id='web:local'").fetchone()
        self.service._check_row(row)
        self.service.deliver_pending()
        with self.app.storage.connection() as conn:
            messages = conn.execute(
                "SELECT content FROM messages WHERE session_id='web-session' AND role='assistant' ORDER BY id"
            ).fetchall()
        self.assertEqual(len(messages), 1)
        self.assertIn("обновилась", str(messages[0]["content"]))


if __name__ == "__main__":
    unittest.main()
