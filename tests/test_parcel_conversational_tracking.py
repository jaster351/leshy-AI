from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest

from leshy.parcel_presenter import format_parcel_result_short
from leshy.parcel_tracking import (
    ParcelResult,
    ParcelTrackingService,
    format_parcel_result,
    parse_parcel_page,
)


class _Storage:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


class _App:
    def __init__(self, storage):
        self.storage = storage


class ParcelConversationalTrackingTests(unittest.TestCase):
    def test_carrier_label_is_never_returned_as_parcel_status(self):
        result = parse_parcel_page(
            "11751925511565",
            "11751925511565\nCarrier\nTracking\nWhere is my parcel?\nEnter your tracking number",
            "https://parcelradar.app/track/?track=11751925511565",
        )
        self.assertNotEqual(result.status.strip().lower(), "carrier")
        self.assertIn("не показал подтверждённый", result.status.lower())

    def test_last_event_and_location_are_shown_in_human_answer(self):
        result = parse_parcel_page(
            "11751925511565",
            "11751925511565\nIn transit\n2026-11-02 18:10\nMoscow, Russia\nCarrier\nTracking",
            "https://parcelradar.app/track/?track=11751925511565",
        )
        self.assertEqual(result.status_category, "in_transit")
        self.assertIn("Посылка в пути", result.status)
        self.assertIn("Moscow", result.status)
        answer = format_parcel_result(result, offer_follow=True)
        self.assertIn("Хотите, чтобы я следил", answer)
        self.assertNotIn("Источник:", answer)
        self.assertNotIn("Проверено", answer)

    def test_short_presenter_accepts_follow_offer_contract(self):
        result = ParcelResult(
            tracking_number="11751925511565",
            status="Посылка в пути. Последнее событие: Moscow",
            status_category="in_transit",
            last_event_text="Moscow",
            source_url="https://parcelradar.app/track/?track=11751925511565",
            checked_at="2026-11-02T18:10:00+00:00",
            fingerprint="initial",
            excerpt="",
        )
        answer = format_parcel_result_short(result, offer_follow=True)
        self.assertIn("11751925511565", answer)
        self.assertIn("Хотите, чтобы я следил", answer)

    def test_confirmation_activates_monitoring_and_delivery_removes_subscription(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = _Storage(Path(tmp) / "parcel.sqlite3")
            service = ParcelTrackingService(_App(storage), runner=object())
            service.start = lambda: None
            initial = ParcelResult(
                tracking_number="11751925511565",
                status="Посылка в пути. Последнее событие: Moscow",
                status_category="in_transit",
                last_event_text="Moscow",
                source_url="https://parcelradar.app/track/?track=11751925511565",
                checked_at="2026-11-02T18:10:00+00:00",
                fingerprint="initial",
                excerpt="",
            )
            self.assertTrue(
                service.offer_follow(
                    "telegram:1",
                    initial,
                    notify_channel="telegram",
                    notify_target="1",
                    session_id="telegram:1:1",
                )
            )
            pending = service.pending_follow("telegram:1", "telegram:1:1")
            self.assertIsNotNone(pending)
            self.assertEqual(pending["state"], "awaiting_confirmation")

            activated = service.confirm_pending("telegram:1", "11751925511565")
            self.assertIsNotNone(activated)
            self.assertEqual(activated["state"], "active")

            with storage.connection() as conn:
                row = conn.execute(
                    "SELECT * FROM parcel_subscriptions WHERE owner_id=? AND tracking_number=?",
                    ("telegram:1", "11751925511565"),
                ).fetchone()
            delivered = ParcelResult(
                tracking_number="11751925511565",
                status="Посылка доставлена",
                status_category="delivered",
                last_event_text="Delivered",
                source_url="https://parcelradar.app/track/?track=11751925511565",
                checked_at="2026-11-03T10:00:00+00:00",
                fingerprint="delivered",
                excerpt="",
            )
            service.track_once = lambda *_args, **_kwargs: delivered
            service._check_row(row)

            with storage.connection() as conn:
                subscription = conn.execute(
                    "SELECT 1 FROM parcel_subscriptions WHERE owner_id=? AND tracking_number=?",
                    ("telegram:1", "11751925511565"),
                ).fetchone()
                notification = conn.execute(
                    "SELECT text FROM parcel_notifications WHERE owner_id=? AND tracking_number=? ORDER BY id DESC LIMIT 1",
                    ("telegram:1", "11751925511565"),
                ).fetchone()
            self.assertIsNone(subscription)
            self.assertIsNotNone(notification)
            self.assertIn("доставлена", notification["text"].lower())
            self.assertIn("удаляю", notification["text"].lower())


if __name__ == "__main__":
    unittest.main()
