from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.browser_skills import BrowserSkillExecution
from leshy.parcel_tracking import (
    PARCELRADAR_SKILL,
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
            "Tracking number\n"
            f"{TRACK}\n"
            "China Post\n"
            "In transit\n"
            "Arrived at sorting center\n"
            "Chelyabinsk\n"
            "02 November 2026 13:40\n"
        )

    def run_form_skill(self, skill, value, *, activity=None):
        self.calls.append(value)
        if activity:
            activity("browser_skill_opening", {"status": "Открываю сайт", "skill_id": skill.skill_id})
        return BrowserSkillExecution(
            skill_id=skill.skill_id,
            status="success",
            page_url="https://parcelradar.app/track/" + value,
            page_title="ParcelRadar tracking",
            visible_text=self.text.replace(TRACK, value),
            input_value=value,
            browser_name="Firefox",
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

    def test_parcelradar_skill_uses_free_no_signup_tracker(self) -> None:
        self.assertEqual(PARCELRADAR_SKILL.start_url, "https://parcelradar.app/")
        self.assertIn("Enter tracking number", PARCELRADAR_SKILL.input_hints)
        self.assertIn("Track", PARCELRADAR_SKILL.submit_hints)
        self.assertEqual(PARCELRADAR_SKILL.allowed_hosts, ("parcelradar.app",))

    def test_page_parser_extracts_english_status(self) -> None:
        parsed = parse_parcel_page(TRACK, self.runner.text, "https://parcelradar.app/track/test", browser_name="Firefox")
        self.assertEqual(parsed.tracking_number, TRACK)
        self.assertEqual(parsed.status_category, "in_transit")
        self.assertIn("посылка в пути", parsed.status.lower())
        self.assertIn("in transit", parsed.last_event_text.lower())
        self.assertTrue(parsed.fingerprint)
        self.assertEqual(parsed.browser_name, "Firefox")

    def test_page_parser_understands_not_found(self) -> None:
        parsed = parse_parcel_page(
            TRACK,
            f"{TRACK}\nShipment not found\nCheck the number and try again later",
            "https://parcelradar.app/track/test",
            browser_name="Firefox",
        )
        self.assertEqual(parsed.status_category, "unknown")
        self.assertIn("не найдены", parsed.status)

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
            "Delivered to recipient\n"
            "Chelyabinsk\n"
            "02 November 2026 18:00\n"
        )
        with self.app.storage.connection() as conn:
            row = conn.execute("SELECT * FROM parcel_subscriptions WHERE owner_id='telegram:123'").fetchone()
        self.service._check_row(row)
        self.service.deliver_pending()
        self.assertEqual(len(self.app.telegram.messages), 1)
        self.assertEqual(self.app.telegram.messages[0][0], 555)
        self.assertIn("доставлена", self.app.telegram.messages[0][1].lower())
        self.assertIn("удаляю", self.app.telegram.messages[0][1].lower())
        with self.app.storage.connection() as conn:
            row_after_delivery = conn.execute(
                "SELECT * FROM parcel_subscriptions WHERE owner_id='telegram:123'"
            ).fetchone()
        self.assertIsNone(row_after_delivery)
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
        self.runner.text = f"{TRACK}\nDelivered to recipient\n"
        with self.app.storage.connection() as conn:
            row = conn.execute("SELECT * FROM parcel_subscriptions WHERE owner_id='web:local'").fetchone()
        self.service._check_row(row)
        self.service.deliver_pending()
        with self.app.storage.connection() as conn:
            messages = conn.execute(
                "SELECT content FROM messages WHERE session_id='web-session' AND role='assistant' ORDER BY id"
            ).fetchall()
        self.assertEqual(len(messages), 1)
        self.assertIn("доставлена", str(messages[0]["content"]).lower())
        self.assertIn("удаляю", str(messages[0]["content"]).lower())


if __name__ == "__main__":
    unittest.main()
