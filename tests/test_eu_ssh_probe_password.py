"""Offline authentication tests for password-only EU TCP/22 probe.

The numeric password is never sent in cleartext. Both directions use HMAC-SHA256.
No actual network traffic or SSH login is performed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from leshy.eu_ssh_probe import _remote_ssh22


PASSWORD = "1234567890" * 7


def load_agent():
    path = Path(__file__).resolve().parents[1] / "tools/eu_ssh_probe_server.py"
    spec = importlib.util.spec_from_file_location("eu_ssh_password_probe_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Standalone EU probe is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PasswordAuthTests(unittest.TestCase):
    def test_server_hmac_valid_and_wrong_password_rejected(self):
        agent = load_agent()
        nonce = "0123456789abcdef0123456789abcdef"
        timestamp = int(__import__("time").time())
        target = "1.1.1.1"
        signature = hmac.new(
            PASSWORD.encode(), f"v1|{target}|{nonce}|{timestamp}|22".encode(),
            hashlib.sha256,
        ).hexdigest()
        handler = SimpleNamespace(server=SimpleNamespace(password=PASSWORD))
        body = {"timestamp": timestamp, "password": signature}
        self.assertTrue(agent.Handler._authorized(handler, body, target, nonce))
        self.assertFalse(agent.Handler._authorized(
            handler, {**body, "password": "0" * 64}, target, nonce,
        ))
        self.assertFalse(agent.Handler._authorized(
            handler, body, "1.0.0.1", nonce,
        ))
        self.assertFalse(agent.Handler._authorized(
            handler, {**body, "timestamp": timestamp - 120}, target, nonce,
        ))

    def test_replayed_nonce_is_rejected(self):
        agent = load_agent()
        nonce = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
        self.assertTrue(agent._new_nonce(nonce))
        self.assertFalse(agent._new_nonce(nonce))

    def test_client_does_not_send_cleartext_password_and_checks_response(self):
        env = {
            "LESHY_EU_PROBE_URL": "http://127.0.0.1:8768",
            "LESHY_EU_PROBE_PASSWORD": PASSWORD,
            "LESHY_EU_PROBE_EXPECTED_COUNTRY": "DE",
        }
        def build_response(req):
            request_body = json.loads(req.data.decode("utf-8"))
            self.assertNotIn(PASSWORD, req.data.decode("utf-8"))
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.full_url, env["LESHY_EU_PROBE_URL"] + "/v1/ssh/check")
            self.assertEqual(request_body["target"], "1.1.1.1")
            expected_request = hmac.new(
                PASSWORD.encode(),
                f"v1|1.1.1.1|{request_body['nonce']}|{request_body['timestamp']}|22".encode(),
                hashlib.sha256,
            ).hexdigest()
            self.assertEqual(request_body["password"], expected_request)
            result = {
                "version": 1, "target": request_body["target"],
                "port": 22, "nonce": request_body["nonce"],
                "country_code": "DE", "node": "europe-1",
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "completed": True,
                "attempts": [
                    {"status": "connected", "elapsed_ms": 5}
                    for _ in range(3)
                ],
            }
            canonical = json.dumps(
                result, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode()
            result["signature"] = hmac.new(
                PASSWORD.encode(), canonical, hashlib.sha256,
            ).hexdigest()
            return result

        class Response:
            status = 200
            def __init__(self, data):
                self.data = data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, count):
                return self.data[:count]

        class Opener:
            def __init__(self, tamper=False):
                self.tamper = tamper
            def open(self, req, timeout):
                result = build_response(req)
                if self.tamper:
                    result["attempts"][0]["status"] = "timeout"
                return Response(json.dumps(result).encode())

        with patch.dict("os.environ", env):
            with patch("leshy.eu_ssh_probe.request.build_opener", return_value=Opener()):
                response = _remote_ssh22("1.1.1.1", timeout=5)
            self.assertEqual(len(response["attempts"]), 3)
            self.assertTrue(all(x["status"] == "connected" for x in response["attempts"]))

            with patch("leshy.eu_ssh_probe.request.build_opener", return_value=Opener(tamper=True)):
                with self.assertRaisesRegex(ValueError, "подпись"):
                    _remote_ssh22("1.1.1.1", timeout=5)


if __name__ == "__main__":
    unittest.main()
