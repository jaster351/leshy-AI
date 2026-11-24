"""Регрессии отображения доставки LiveMentor, без сетевого обмена."""
import tempfile
import unittest
from pathlib import Path

from leshy.mentor_batch import digest, encode
from leshy.mentor_view import mentor_snapshot


class MentorDeliveryViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = Path(self.tmp.name)
        self.root = self.brain / "ai-mentor"
        self.root.mkdir()
        self.batch = "mentor-batch-test"
        self.raw = encode({
            "schema": "ai-mentor.batch.v1", "source": "AI-mentor",
            "batch_id": self.batch, "mode": "sequential",
            "status": "not_run", "exchange_interval_seconds": 3600,
            "questions": [{"id": "q01", "question": "Учебный пример"}],
        })
        self.path = self.root / (self.batch + ".delivery.json")
        (self.root / "config.json").write_bytes(encode({
            "enabled": True, "repository": "jaster351/AI-mentor",
            "batch_id": self.batch, "batch_sha256": digest(self.raw),
        }))

    def test_delivery_visible_before_inference(self):
        self.path.write_bytes(self.raw)
        result = mentor_snapshot(self.brain)
        self.assertEqual(result["delivered_count"], 1)
        self.assertEqual(len(result["items"]), 1)
        row = result["items"][0]
        self.assertEqual(row["status"], "delivered")
        self.assertEqual(row["question"], "Учебный пример")
        self.assertEqual(row["answer"], "")
        self.assertEqual(row["received_at"], "")
        self.assertTrue(row["delivered_at"])

    def test_invalid_digest_not_displayed(self):
        self.path.write_bytes(self.raw + b" ")
        result = mentor_snapshot(self.brain)
        self.assertEqual(result["delivered_count"], 0)
        self.assertEqual(result["items"], [])

    def test_missing_delivery_not_counted(self):
        result = mentor_snapshot(self.brain)
        self.assertEqual(result["delivered_count"], 0)
        self.assertEqual(result["items"], [])

    def test_runtime_receipt_distinct_from_delivery(self):
        self.path.write_bytes(self.raw)
        rid = self.batch + "-q01"
        (self.root / (rid + ".question.json")).write_bytes(encode({
            "request_id": rid, "question": "Учебный пример",
            "batch_sha256": digest(self.raw), "timestamp": "2026-11-25T00:00:00+00:00",
        }))
        row = mentor_snapshot(self.brain)["items"][0]
        self.assertEqual(row["status"], "received")
        self.assertTrue(row["received_at"])
        self.assertTrue(row["delivered_at"])
