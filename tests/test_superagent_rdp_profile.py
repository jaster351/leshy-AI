"""RDP launch profile tests: no network, shell, credentials or desktop access."""
from __future__ import annotations

import json
import types
import unittest

from leshy.superagent_remote_permissions import RemotePermissionPolicy
from leshy.superagent_rdp_profile import rdp_connection_profile
from test_superagent_verified_ssh import HOST, PIN, Storage


class RdpProfileTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = types.SimpleNamespace(
            storage=self.storage,
            owner_for_web_cookie=lambda token: "alice" if token == "valid-cookie" else "",
        )
        self.policy = RemotePermissionPolicy(
            self.identity, enrollment_verifier=lambda *_: True,
        )
        self.actor = self.policy.actor_from_web_cookie("valid-cookie")
        self.policy.register_verified_node(
            self.actor, HOST, PIN, enrollment_proof="verified-by-owner"
        )

    def tearDown(self):
        self.storage.db.close()

    def test_owner_gets_safe_native_profile_without_password(self):
        profile = rdp_connection_profile(self.policy, self.actor, HOST)
        self.assertEqual(profile["node_id"], HOST)
        self.assertEqual(profile["filename"], "Leshy-RDP-192-168-5-12.rdp")
        self.assertIn("full address:s:192.168.5.12:3389", profile["rdp"])
        self.assertIn("enablecredsspsupport:i:1", profile["rdp"])
        self.assertIn("prompt for credentials:i:1", profile["rdp"])
        self.assertIn("authentication level:i:2", profile["rdp"])
        self.assertIn("redirectclipboard:i:0", profile["rdp"])
        self.assertIn("redirectdrives:i:0", profile["rdp"])
        self.assertFalse(profile["remote_desktop_control_by_agent"])
        self.assertTrue(profile["requires_user_login"])
        self.assertNotIn(PIN, json.dumps(profile))
        for secret in ("password", "secret", "username:s:", "loadbalanceinfo", "password 51:b:"):
            self.assertNotIn(secret, profile["rdp"].lower())

    def test_unregistered_or_other_owner_denied(self):
        for invalid in ("192.168.9.20", "example.com", "127.0.0.1", "169.254.169.254"):
            with self.subTest(invalid=invalid):
                with self.assertRaises((ValueError, PermissionError)):
                    rdp_connection_profile(self.policy, self.actor, invalid)
        other = type(self.actor)("bob", self.actor.session_fingerprint, "web")
        with self.assertRaises(PermissionError):
            rdp_connection_profile(self.policy, other, HOST)

    def test_owner_session_must_remain_live(self):
        with self.storage.connection() as db:
            db.execute("DELETE FROM owner_web_sessions WHERE owner_id=?", ("alice",))
        with self.assertRaises(PermissionError):
            rdp_connection_profile(self.policy, self.actor, HOST)

    def test_ipv6_has_explicit_unsupported_state(self):
        ipv6 = "2001:4860:4860::8888"
        self.policy.register_verified_node(
            self.actor, ipv6, PIN, enrollment_proof="verified-by-owner")
        with self.assertRaisesRegex(ValueError, "IPv4"):
            rdp_connection_profile(self.policy, self.actor, ipv6)


if __name__ == "__main__":
    unittest.main()
