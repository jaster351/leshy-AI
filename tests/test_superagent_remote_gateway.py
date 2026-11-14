"""Guacamole gateway settings contracts: no secrets, SSRF or cross-owner access."""
from __future__ import annotations

import hashlib
import json
import types
import unittest

from leshy.superagent_remote_permissions import RemotePermissionPolicy
from leshy.superagent_remote_gateway import (
    validate_gateway_url, get_remote_gateway, set_remote_gateway,
)
from test_superagent_verified_ssh import Storage


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = types.SimpleNamespace(
            storage=self.storage,
            owner_for_web_cookie=lambda token: "alice" if token == "valid-cookie" else "",
        )
        self.policy = RemotePermissionPolicy(self.identity, enrollment_verifier=lambda *_: True)
        self.owner = self.policy.actor_from_web_cookie("valid-cookie")

    def tearDown(self):
        self.storage.db.close()

    def test_valid_https_gateway_and_loopback(self):
        self.assertEqual(
            validate_gateway_url("https://gateway.example.net/guacamole/"),
            "https://gateway.example.net/guacamole/",
        )
        self.assertEqual(
            validate_gateway_url("https://gateway.example.net/guacamole"),
            "https://gateway.example.net/guacamole/",
        )
        self.assertEqual(
            validate_gateway_url("http://127.0.0.1:8088/guacamole/"),
            "http://127.0.0.1:8088/guacamole/",
        )
        self.assertEqual(
            validate_gateway_url("http://[::1]:8088/guacamole"),
            "http://[::1]:8088/guacamole/",
        )

    def test_untrusted_url_never_saved(self):
        for url in (
            "http://example.com/guacamole/",
            "http://192.168.0.1:8080/guacamole/",
            "http://127.1:8088/guacamole/",
            "http://localhost:8088/guacamole/",
            "javascript:alert(1)", "data:text/html,...",
            "file:///etc/passwd", "https://evil.test@host.example/guacamole/",
            "https://example.com/guacamole/?token=secret",
            "https://example.com/guacamole/#/client",
            "https://example.com/../../steal",
            "https://example.com/guacamole.evil",
            "https://example.com.local/guacamole/",
            "https://example.com.internal/guacamole/",
            "https://169.254.169.254/guacamole/",
            "https://[::]/guacamole/",
            "https://example.com:99999/guacamole/",
            "https://example.com:0/guacamole/",
            "",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_gateway_url(url)

    def test_persistence_and_owner_isolation(self):
        empty = get_remote_gateway(self.policy, self.owner)
        self.assertFalse(empty["configured"])
        saved = set_remote_gateway(
            self.policy, self.owner, "https://remote.example.net/guacamole/"
        )
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["authentication"], "separate_guacamole_login")
        self.assertFalse(saved["ai_desktop_control"])
        with self.storage.connection() as db:
            stored = db.execute(
                "SELECT * FROM superagent_owner_remote_gateway WHERE owner_id=?",
                ("alice",),
            ).fetchone()
        self.assertEqual(stored["gateway_url"], saved["url"])
        impostor = type(self.owner)("alice", "not-a-live-session", "web")
        with self.assertRaises(PermissionError):
            get_remote_gateway(self.policy, impostor)
        other = type(self.owner)("bob", self.owner.session_fingerprint, "web")
        with self.assertRaises(PermissionError):
            set_remote_gateway(self.policy, other, "https://fake.example/guacamole/")
        reset = set_remote_gateway(self.policy, self.owner, "")
        self.assertFalse(reset["configured"])
        self.assertFalse(get_remote_gateway(self.policy, self.owner)["configured"])

    def test_url_does_not_include_credentials_or_tokens(self):
        info = set_remote_gateway(
            self.policy, self.owner, "https://gateway.example.org/guacamole/"
        )
        output = json.dumps(info)
        self.assertNotIn("password", output)
        self.assertNotIn("token", output)
        self.assertNotIn("cookie", output)
        self.assertNotIn("username", output)
        self.assertEqual(info["protocols"], ["ssh", "rdp"])

    def test_revoked_owner_session_fails_closed(self):
        set_remote_gateway(self.policy, self.owner, "https://secure.example/guacamole/")
        with self.storage.connection() as db:
            db.execute("DELETE FROM owner_web_sessions WHERE owner_id=?", ("alice",))
        with self.assertRaises(PermissionError):
            get_remote_gateway(self.policy, self.owner)


if __name__ == "__main__":
    unittest.main()
