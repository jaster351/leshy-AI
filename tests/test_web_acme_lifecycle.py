"""Регрессии состояния ACME и bind без сервера, CA, ключей и настоящих потоков.

Не запускать без отдельного разрешения владельца: разрешение семи TLS-тестов
относится только к tests/test_web_transport_security.py.
"""
from __future__ import annotations

from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

from leshy.web_acme import WebAcmeService, _AcmeFailure
from leshy.web_transport_runtime import WebTransportRuntime


TERMS = "https://letsencrypt.org/repository/"


class AcmeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Внешняя сеть запрещена"))
        self.network.start()
        self.addCleanup(self.network.stop)
        wire = patch("leshy.web_acme._AcmeWire")
        self.wire = wire.start().return_value
        self.wire.discover.return_value = TERMS
        self.addCleanup(wire.stop)
        self.service = WebAcmeService(Path(self.directory.name), Mock())
        self.body = {"confirm": True, "domains": ["leshy.example.org"], "email": "owner@example.org",
                     "environment": "staging", "auto_renew": True, "terms_url": TERMS,
                     "terms_agreed": True, "contact_consent": True}
        self.service.configure(self.body)

    def assert_manual_abandon_required(self):
        self.assertTrue(self.service.config["attention_required"])
        self.assertTrue(self.service.config["outcome_unknown"])
        with patch.object(self.service, "_launch") as launch:
            with self.assertRaises(ValueError):
                self.service.issue({"confirm": True})
            launch.assert_not_called()
            self.service.issue({"confirm": True, "abandon_unknown": True})
            launch.assert_called_once()

    def test_saving_during_issue_retains_unknown_outcome(self):
        self.service.config["state"] = "issuing"
        self.service._job = Mock(is_alive=Mock(return_value=True))
        generation = self.service._generation
        self.service.configure(self.body)
        self.assertGreater(self.service._generation, generation)
        self.service._job.is_alive.return_value = False
        self.assert_manual_abandon_required()

    def test_disable_enable_does_not_clear_unknown_result(self):
        self.service.config["state"] = "issuing"
        self.service._job = Mock(is_alive=Mock(return_value=True))
        self.service.configure({"enabled": False, "confirm": True})
        self.assertEqual(self.service.config["consent_scope"], "")
        self.service._job.is_alive.return_value = False
        self.service.configure(self.body)
        self.assert_manual_abandon_required()

    def test_new_scope_does_not_clear_previous_unknown_result(self):
        self.service.config.update(attention_required=True, outcome_unknown=True)
        self.service.configure({**self.body, "domains": ["other.example.org"]})
        self.assert_manual_abandon_required()

    def test_definite_failure_accepts_explicit_manual_retry(self):
        self.service.config.update(state="error", attention_required=True, outcome_unknown=False)
        with patch.object(self.service, "_launch") as launch:
            with self.assertRaises(ValueError):
                self.service.issue({"confirm": False})
            launch.assert_not_called()
            self.service.issue({"confirm": True})
            launch.assert_called_once()

    def test_failed_import_restores_authorized_renewal(self):
        previous = dict(self.service.config)
        generation = self.service._generation
        with self.assertRaises(OSError):
            self.service.replace_certificate_manually(Mock(side_effect=OSError("Учебная ошибка записи")))
        self.assertEqual(self.service.config, previous)
        self.assertGreater(self.service._generation, generation)
        reopened = WebAcmeService(Path(self.directory.name), Mock())
        self.assertEqual(reopened.config, previous)

    def test_failed_import_preserves_unknown_interrupted_job(self):
        self.service.config["state"] = "issuing"
        self.service._job = Mock(is_alive=Mock(return_value=True))
        with self.assertRaises(OSError):
            self.service.replace_certificate_manually(Mock(side_effect=OSError("Учебная ошибка записи")))
        self.assertTrue(self.service.config["enabled"])
        self.assert_manual_abandon_required()

    def test_attention_blocks_automatic_renewal(self):
        self.service.config.update(enabled=True, auto_renew=True, renew_at=1.0, attention_required=True)
        with patch.object(self.service, "_stop") as stop, patch.object(self.service, "_launch") as launch:
            stop.wait.side_effect = [False, True]
            self.service._renew_loop()
            launch.assert_not_called()

    def test_retry_after_is_respected_until_deadline(self):
        clock = [0.0]
        def wait(seconds):
            clock[0] += seconds
        guard = Mock()
        with patch("leshy.web_acme.time.monotonic", side_effect=lambda: clock[0]), patch.object(self.service, "_stop") as stop:
            stop.wait.side_effect = wait
            self.service._pause({"retry-after": "25"}, guard, 60.0)
            self.assertGreaterEqual(clock[0], 25.0)
            self.assertGreater(guard.call_count, 20)
            clock[0] = 0.0
            with self.assertRaises(_AcmeFailure):
                self.service._pause({"retry-after": "90"}, guard, 7.0)
            self.assertEqual(clock[0], 7.0)


class IndependentListenerTests(unittest.TestCase):
    def test_busy_http_does_not_prevent_https_bind(self):
        runtime = WebTransportRuntime.__new__(WebTransportRuntime)
        runtime.lock = threading.RLock()
        runtime._servers = {}
        runtime._states = {name: {} for name in ("http", "https")}
        def bind(protocol):
            if protocol == "http":
                raise OSError("Учебный занятый порт")
            runtime._servers[protocol] = Mock()
        with patch.object(runtime, "_bind", side_effect=bind) as operation:
            runtime.bind(Mock())
        self.assertEqual([call.args[0] for call in operation.call_args_list], ["http", "https"])
        self.assertEqual(runtime._states["http"]["status"], "bind_failed")
        self.assertIn("https", runtime._servers)


if __name__ == "__main__":
    unittest.main()
