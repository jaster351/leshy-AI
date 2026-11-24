"""Регрессии локального учебного цикла при сбоях транспорта."""
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib import error

from leshy.mentor_batch import BatchRunner, digest, encode, is_transient_transport_error


class MentorOfflineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.batch = "mentor-batch-test"
        self.rid = self.batch + "-q01"
        self.raw = encode({
            "schema": "ai-mentor.batch.v1", "source": "AI-mentor",
            "batch_id": self.batch, "mode": "sequential",
            "status": "not_run", "exchange_interval_seconds": 3600,
            "questions": [{"id": "q01", "question": "Учебный пример"}],
        })
        self.config = {
            "enabled": True, "repository": "jaster351/AI-mentor",
            "batch_id": self.batch, "batch_sha256": digest(self.raw),
        }
        (self.root / "config.json").write_bytes(encode(self.config))
        (self.root / (self.batch + ".delivery.json")).write_bytes(self.raw)
        self.app = SimpleNamespace(settings=SimpleNamespace(llm_backend="unavailable"))
        for p in (patch.object(BatchRunner, "uses_local_delivery", return_value=True),
                  patch("leshy.mentor_batch.load_secret", return_value="synthetic-credential")):
            p.start()
            self.addCleanup(p.stop)

    def runner(self, gh):
        return BatchRunner(self.root, self.app, threading.Event(), gh)

    def test_network_failure_keeps_local_question_and_receipt(self):
        def gh(*args):
            raise error.URLError("offline")
        result = self.runner(gh).cycle(self.config)
        self.assertEqual(result, "waiting_connection")
        self.assertTrue((self.root / (self.rid + ".question.json")).exists())
        self.assertTrue((self.root / (self.batch + ".receipt.json")).exists())
        self.assertFalse((self.root / (self.batch + ".receipt-published.json")).exists())

    def test_existing_answer_exported_locally_without_network(self):
        (self.root / (self.rid + ".json")).write_bytes(encode({
            "request_id": self.rid, "batch_sha256": digest(self.raw),
            "question_sha256": digest("Учебный пример".encode("utf-8")),
            "status": "answered", "source": "leshy_model",
            "answer": "Ответ", "timestamp": "2026-11-25T00:00:00+00:00",
            "revision": "1.10.219", "duration_ms": 50,
        }))
        def gh(*args):
            raise TimeoutError("offline")
        self.assertEqual(self.runner(gh).cycle(self.config), "waiting_connection")
        self.assertTrue((self.root / "export.json").exists())
        self.assertFalse((self.root / (self.rid + ".published.json")).exists())

    def test_forbidden_not_treated_as_offline(self):
        def gh(*args):
            raise error.HTTPError("https://api.github.com", 403, "denied", None, None)
        with self.assertRaises(error.HTTPError):
            self.runner(gh).cycle(self.config)
        self.assertFalse((self.root / (self.rid + ".question.json")).exists())

    def test_public_repository_rejected(self):
        with self.assertRaises(PermissionError):
            self.runner(lambda *args: {"private": False}).cycle(self.config)
        self.assertFalse((self.root / (self.rid + ".question.json")).exists())

    def test_missing_token_not_treated_as_offline(self):
        with patch("leshy.mentor_batch.load_secret", return_value=""):
            result = self.runner(lambda *args: self.fail("network called")).cycle(self.config)
        self.assertEqual(result, "not_configured")

    def test_corrupt_local_package_rejected_offline(self):
        (self.root / (self.batch + ".delivery.json")).write_bytes(self.raw + b" ")
        def gh(*args):
            raise TimeoutError("offline")
        with self.assertRaises(PermissionError):
            self.runner(gh).cycle(self.config)

    def test_classification(self):
        for status in (401, 403, 404, 422):
            self.assertFalse(is_transient_transport_error(
                error.HTTPError("https://api.github.com", status, "", None, None)))
        for status in (408, 429, 500, 502, 503, 504):
            self.assertTrue(is_transient_transport_error(
                error.HTTPError("https://api.github.com", status, "", None, None)))

    def test_tls_error_is_not_transient(self):
        self.assertFalse(is_transient_transport_error(error.URLError(ssl.SSLCertVerificationError("bad certificate"))))

    def test_wrong_private_repository_rejected(self):
        with self.assertRaises(PermissionError):
            self.runner(lambda *args: {"private": True, "id": 1, "full_name": "other/repo"}).cycle(self.config)
        self.assertFalse((self.root / (self.rid + ".question.json")).exists())

    def test_revoked_local_token_stops_offline_cycle(self):
        def gh(*args):
            self.revoked = True
            raise error.URLError("offline")
        self.revoked = False
        def token(*args):
            return "" if self.revoked else "synthetic-credential"
        with patch("leshy.mentor_batch.load_secret", side_effect=token):
            with self.assertRaises(PermissionError):
                self.runner(gh).cycle(self.config)
        self.assertFalse((self.root / (self.rid + ".json")).exists())
