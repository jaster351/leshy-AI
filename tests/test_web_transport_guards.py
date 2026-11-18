"""Восемь изолированных регрессий transport guard и browser-ready callback.

Запуск требует отдельного разрешения владельца. Прежние разрешения 7+9
проверок других файлов сюда не распространяются. Сервер/CA не запускаются.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from leshy.web_acme import _AcmeFailure
from leshy.web_transport_runtime import WebTransportRuntime, listener_ready
from leshy.web_transport_settings import WebTransportSettings


HOST = "leshy.example.org"
TERMS = "https://letsencrypt.org/repository/"


class WebTransportGuardTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for key in ("LESHY_PORT", "LESHY_HTTPS_PORT"):
            os.environ.pop(key, None)
        for operation in ("connect", "bind"):
            blocked = patch.object(socket.socket, operation,
                                   side_effect=AssertionError("Настоящая сеть запрещена"))
            blocked.start()
            self.addCleanup(blocked.stop)
        thread = patch("leshy.web_acme.threading.Thread",
                       side_effect=AssertionError("Настоящие потоки запрещены"))
        self.thread = thread.start()
        self.addCleanup(thread.stop)
        wire = patch("leshy.web_acme._AcmeWire")
        self.wire = wire.start().return_value
        self.wire.discover.return_value = TERMS
        self.addCleanup(wire.stop)

    def runtime(self, *, saved=None, explicit_http_port=None, host="127.0.0.1"):
        settings = WebTransportSettings(self.root)
        settings.save({"hostname": HOST, **(saved or {}), "confirm": True})
        return WebTransportRuntime(self.root, host, explicit_http_port)

    def certificate(self, runtime):
        context = patch.object(runtime.certificates, "context", return_value=object())
        metadata = patch.object(runtime.certificates, "status",
                                return_value={"valid": True, "dns_names": [HOST], "ip_addresses": []})
        context_mock, metadata_mock = context.start(), metadata.start()
        self.addCleanup(context.stop)
        self.addCleanup(metadata.stop)
        return context_mock, metadata_mock

    @staticmethod
    def consent(auto_renew=True):
        return {"confirm": True, "domains": [HOST], "email": "owner@example.org",
                "environment": "staging", "auto_renew": auto_renew,
                "terms_url": TERMS, "terms_agreed": True, "contact_consent": True}

    def test_cli_http_override_rejects_conflict_before_save(self):
        os.environ["LESHY_PORT"] = "9999"
        runtime = self.runtime(saved={"http_port": 9000}, explicit_http_port=8765)
        self.assertEqual(runtime.configuration["http_port"], 8765)
        before = runtime.settings.path.read_bytes()
        with patch.object(runtime.settings, "save", wraps=runtime.settings.save) as save:
            with self.assertRaisesRegex(ValueError, "заданных при запуске"):
                runtime.save_settings({"https_port": 8765, "confirm": True})
            save.assert_not_called()
        self.assertEqual(runtime.settings.path.read_bytes(), before)

    def test_https_environment_override_rejects_conflict_before_save(self):
        os.environ["LESHY_HTTPS_PORT"] = "9443"
        runtime = self.runtime(saved={"https_port": 9000})
        before = runtime.settings.path.read_bytes()
        with patch.object(runtime.settings, "save", wraps=runtime.settings.save) as save:
            with self.assertRaisesRegex(ValueError, "заданных при запуске"):
                runtime.save_settings({"http_port": 9443, "confirm": True})
            save.assert_not_called()
        self.assertEqual(runtime.settings.path.read_bytes(), before)

    def test_http_off_rejected_during_renewal_or_active_job(self):
        runtime = self.runtime()
        self.certificate(runtime)
        runtime._states["http"]["listening"] = True
        runtime.acme.configure(self.consent())
        before = runtime.settings.path.read_bytes()
        with patch.object(runtime.settings, "save", wraps=runtime.settings.save) as save:
            with self.subTest(state="autorenew"), self.assertRaisesRegex(ValueError, "автопродление"):
                runtime.save_settings({"http_enabled": False, "confirm": True})
            runtime.acme.configure({"enabled": False, "confirm": True})
            runtime.acme._job = Mock(is_alive=Mock(return_value=True))
            with self.subTest(state="job"), self.assertRaisesRegex(ValueError, "автопродление"):
                runtime.save_settings({"http_enabled": False, "confirm": True})
            save.assert_not_called()
        self.assertEqual(runtime.settings.path.read_bytes(), before)

    def test_issue_requires_http_and_starts_once_when_ready(self):
        runtime = self.runtime(saved={"http_enabled": False})
        runtime.acme.configure(self.consent(auto_renew=False))
        with self.assertRaisesRegex(_AcmeFailure, "HTTP-01"):
            runtime.acme.issue({"confirm": True})
        self.thread.assert_not_called()
        self.assertNotEqual(runtime.acme.config["state"], "issuing")
        runtime.settings.save({"http_enabled": True, "confirm": True})
        ready = WebTransportRuntime(self.root, "127.0.0.1")
        ready._states["http"]["listening"] = True
        with patch("leshy.web_acme.threading.Thread") as thread:
            thread.return_value.is_alive.return_value = True
            ready.acme.issue({"confirm": True})
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            self.assertEqual(ready.acme.config["state"], "issuing")
            with self.assertRaisesRegex(ValueError, "уже выполняется"):
                ready.acme.issue({"confirm": True})
            thread.assert_called_once()

    def test_http_off_during_terms_discovery_cannot_enable_renewal(self):
        runtime = self.runtime()
        self.certificate(runtime)
        runtime._states["http"]["listening"] = True
        def discover_after_transport_change():
            runtime.save_settings({"http_enabled": False, "confirm": True})
            return TERMS
        self.wire.discover.side_effect = discover_after_transport_change
        with self.assertRaisesRegex(_AcmeFailure, "HTTP-01"):
            runtime.acme.configure(self.consent())
        self.assertFalse(runtime.settings.load()["http_enabled"])
        persisted = json.loads(runtime.acme.path.read_text(encoding="utf-8"))
        self.assertFalse(persisted["enabled"])
        self.assertEqual(persisted["state"], "needs_consent")
        self.assertEqual(persisted["consent_scope"], "")
        with self.assertRaises(_AcmeFailure):
            runtime.acme.issue({"confirm": True})
        self.thread.assert_not_called()

    def test_browser_callback_uses_started_https_once_after_http_failure(self):
        order = []
        callback = Mock(side_effect=lambda url: order.append(("browser", url)))
        with listener_ready(callback):
            runtime = self.runtime()
        self.certificate(runtime)
        runtime._states["http"].update(listening=False, status="bind_failed")
        runtime._servers["https"] = Mock()
        def start(protocol):
            order.append("start")
            runtime._states[protocol].update(listening=True, port=9443)
            runtime._threads[protocol] = Mock(is_alive=Mock(return_value=True))
        with patch.object(runtime, "_start_server", side_effect=start), \
                patch.object(runtime.acme, "start", side_effect=lambda: order.append("acme")), \
                patch.object(runtime._stopping, "wait", return_value=True):
            runtime.serve_forever()
            self.assertEqual(order, ["start", "acme", ("browser", "https://" + HOST + ":9443/")])
            runtime.serve_forever()
        callback.assert_called_once_with("https://" + HOST + ":9443/")
        self.thread.assert_not_called()

    def test_tls_target_requires_context_san_and_live_thread(self):
        runtime = self.runtime()
        context, metadata = self.certificate(runtime)
        runtime._states["https"].update(listening=True, port=9443)
        runtime._threads["https"] = Mock(is_alive=Mock(return_value=True))
        with self.subTest(reason="context"):
            context.return_value = None
            self.assertIsNone(runtime.listening_url())
        with self.subTest(reason="SAN"):
            context.return_value = object()
            metadata.return_value = {"dns_names": ["other.example.org"], "ip_addresses": []}
            self.assertIsNone(runtime.listening_url())
        with self.subTest(reason="thread"):
            metadata.return_value = {"dns_names": [HOST], "ip_addresses": []}
            runtime._threads["https"].is_alive.return_value = False
            self.assertIsNone(runtime.listening_url())

    def test_ipv6_http_target_uses_actual_bound_port(self):
        runtime = self.runtime(host="::")
        runtime._states["http"].update(listening=True, port=19000)
        runtime._threads["http"] = Mock(is_alive=Mock(return_value=True))
        self.assertNotEqual(runtime.configuration["http_port"], 19000)
        self.assertEqual(runtime.listening_url(), "http://[::1]:19000/")


if __name__ == "__main__":
    unittest.main()
