from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.chat_idempotency import ChatRequestLedger, telegram_request_id
from leshy.storage import Storage


class ChatIdempotencyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "leshy.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_request_id_can_be_claimed_only_once_even_by_two_ledgers(self):
        first = ChatRequestLedger(self.storage)
        second = ChatRequestLedger(self.storage)

        claim1 = first.claim(
            "web-request-42",
            owner_id="web:local",
            channel="web-stream",
            session_id="chat-1",
        )
        claim2 = second.claim(
            "web-request-42",
            owner_id="web:local",
            channel="web-stream",
            session_id="chat-1",
        )

        self.assertTrue(claim1["acquired"])
        self.assertFalse(claim2["acquired"])
        self.assertEqual(claim2["status"], "in_progress")

        first.complete(
            "web-request-42",
            {"session_id": "chat-1", "answer": "готовый ответ", "sources": []},
            session_id="chat-1",
        )
        claim3 = second.claim(
            "web-request-42",
            owner_id="web:local",
            channel="web-retry",
            session_id="chat-1",
        )
        self.assertFalse(claim3["acquired"])
        self.assertEqual(claim3["status"], "done")
        self.assertEqual(claim3["response"]["answer"], "готовый ответ")

    def test_ledger_survives_new_storage_instance(self):
        first = ChatRequestLedger(self.storage)
        self.assertTrue(first.claim("persisted-1", owner_id="web:local", channel="web")["acquired"])
        first.complete("persisted-1", {"answer": "один раз", "session_id": "s1"})

        reopened_storage = Storage(Path(self.tmp.name) / "leshy.db")
        reopened = ChatRequestLedger(reopened_storage)
        duplicate = reopened.claim("persisted-1", owner_id="web:local", channel="windows")

        self.assertFalse(duplicate["acquired"])
        self.assertEqual(duplicate["status"], "done")
        self.assertEqual(duplicate["response"]["answer"], "один раз")

    def test_request_id_cannot_be_reused_by_another_owner(self):
        ledger = ChatRequestLedger(self.storage)
        ledger.claim("shared-id", owner_id="web:owner-a", channel="web")
        with self.assertRaises(PermissionError):
            ledger.claim("shared-id", owner_id="web:owner-b", channel="web")

    def test_failed_request_can_be_retried_with_same_id(self):
        ledger = ChatRequestLedger(self.storage)
        self.assertTrue(ledger.claim("retry-1", owner_id="web:local", channel="web")["acquired"])
        ledger.fail("retry-1", "временная ошибка")
        retried = ledger.claim("retry-1", owner_id="web:local", channel="web")
        self.assertTrue(retried["acquired"])
        self.assertEqual(retried["status"], "in_progress")

    def test_telegram_message_has_stable_cross_retry_request_id(self):
        first = telegram_request_id(221168918, 8405)
        second = telegram_request_id("221168918", "8405")
        other = telegram_request_id(221168918, 8406)

        self.assertEqual(first, second)
        self.assertEqual(first, "telegram:221168918:8405")
        self.assertNotEqual(first, other)


if __name__ == "__main__":
    unittest.main()
