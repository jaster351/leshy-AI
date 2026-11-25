"""Проверки статуса ручной доставки без запуска планировщика."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from leshy.mentor_delivery_request import request_state


class MentorDeliveryRequestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.rid = str(uuid4())
        self.created = datetime.now(timezone.utc) - timedelta(seconds=10)

    def put(self, name, payload):
        (self.root / name).write_text(json.dumps(payload), encoding="utf-8")

    def request(self):
        self.put("delivery-request.json", {"schema": "ai-mentor.delivery-request.v1",
                 "request_id": self.rid, "created_at": self.created.isoformat()})

    def result(self, rid=None, timestamp=None, status="delivered"):
        self.put("delivery-request-result.json", {"schema": "ai-mentor.delivery-status.v1",
                 "request_id": rid or self.rid, "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
                 "status": status})

    def test_absent(self):
        self.assertEqual(request_state(self.root), {})

    def test_queued_without_result(self):
        self.request()
        self.assertEqual(request_state(self.root)["state"], "queued")

    def test_old_request_does_not_count(self):
        self.request()
        self.result(rid=str(uuid4()))
        self.assertEqual(request_state(self.root)["state"], "queued")

    def test_old_timestamp_does_not_count(self):
        self.request()
        self.result(timestamp=self.created - timedelta(seconds=1))
        self.assertEqual(request_state(self.root)["state"], "queued")

    def test_result_survives_periodic_check(self):
        self.request()
        self.result(status="up_to_date")
        self.put("delivery-status.json", {"status": "error"})
        self.assertEqual(request_state(self.root)["result"], "up_to_date")

    def test_auth_error_is_not_success(self):
        self.request()
        self.result(status="authorization_error")
        self.assertEqual(request_state(self.root)["result"], "authorization_error")

    def test_expired_request(self):
        self.created -= timedelta(minutes=31)
        self.request()
        self.assertEqual(request_state(self.root)["state"], "unconfirmed")

    def test_bad_id(self):
        self.rid = "bad"
        self.request()
        self.assertEqual(request_state(self.root), {})

    def test_unknown_result(self):
        self.request()
        self.result(status="unknown")
        self.assertEqual(request_state(self.root)["state"], "queued")
