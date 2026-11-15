from datetime import datetime, timezone
import unittest

from leshy.work_schedule import WorkAssistancePolicy, WorkSchedule


class WorkScheduleTests(unittest.TestCase):
    def setUp(self):
        self.schedule = WorkSchedule()
        self.policy = WorkAssistancePolicy(self.schedule)

    def test_monday_to_saturday_11_to_21_moscow(self):
        allowed = (
            datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc),   # Пн 11:00 МСК.
            datetime(2026, 11, 7, 17, 59, tzinfo=timezone.utc), # Сб 20:59 МСК.
        )
        for moment in allowed:
            with self.subTest(moment=moment):
                self.assertTrue(self.schedule.contains(moment))

    def test_outside_shift_and_sunday_are_blocked(self):
        blocked = (
            datetime(2026, 11, 2, 7, 59, tzinfo=timezone.utc),  # Пн 10:59 МСК.
            datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc),  # Пн 21:00 МСК.
            datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc),   # Вс 12:00 МСК.
        )
        for moment in blocked:
            with self.subTest(moment=moment):
                self.assertFalse(self.schedule.contains(moment))

    def test_presence_is_required_by_default(self):
        moment = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # Пн 12:00 МСК.
        self.assertFalse(self.policy.allows(now=moment))
        self.assertTrue(self.policy.allows(now=moment, workstation_active=True))
        self.assertTrue(self.policy.allows(now=moment, manual_work_mode=True))

    def test_presence_never_overrides_schedule(self):
        moment = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)  # Вс 12:00 МСК.
        self.assertFalse(self.policy.allows(now=moment, workstation_active=True))
        self.assertFalse(self.policy.allows(now=moment, manual_work_mode=True))


if __name__ == "__main__":
    unittest.main()
