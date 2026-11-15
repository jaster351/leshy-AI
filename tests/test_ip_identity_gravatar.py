from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from leshy.owner_identity import OwnerIdentityService
from leshy.profile_auth_providers import is_public_https_domain, provider_status
from leshy.storage import Storage


class NatIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.service = OwnerIdentityService(Storage(root / "test.db"), root)
        self.owner1, self.cookie1, self.session1 = self.service.ensure_web_session("", "web:local")

    def test_opt_in_gravatar_hash_normalization_and_removal(self):
        avatar = self.service.configure_gravatar(self.owner1, "  User@Example.org ")
        expected = sha256(b"user@example.org").hexdigest()
        self.assertEqual(
            avatar["url"],
            f"https://gravatar.com/avatar/{expected}?s=96&d=404",
        )
        self.assertNotIn("User@Example.org", str(avatar))
        self.assertTrue(self.service.status(self.owner1)["avatar"]["external_request"])
        self.assertEqual(self.service.clear_gravatar(self.owner1)["provider"], "local")
        self.assertEqual(self.service.status(self.owner1)["avatar"]["url"], "")

    def test_avatar_is_not_identity_or_authentication(self):
        owner2, _, _ = self.service.ensure_web_session("", "web:local")
        self.service.configure_gravatar(self.owner1, "person@example.net")
        self.assertEqual(self.service.status(owner2)["avatar"]["provider"], "local")
        with self.assertRaises(ValueError):
            self.service.configure_gravatar(self.owner1, "not-an-email")
        with self.assertRaises(PermissionError):
            self.service.configure_gravatar("nonexistent-owner", "person@example.net")

    def test_pairing_on_ip_transfers_browser_identity_once(self):
        owner2, cookie2, session2 = self.service.ensure_web_session("", "web:local")
        self.assertNotEqual(owner2, self.owner1)
        pair = self.service.create_browser_pairing(self.owner1, self.session1)
        self.assertRegex(pair["code"], r"^[0-9a-f]{32}$")
        result = self.service.consume_browser_pairing(pair["code"], session2)
        self.assertEqual(result["mode"], "login")
        self.assertEqual(self.service.owner_for_web_cookie(cookie2), self.owner1)
        with self.assertRaises(ValueError):
            self.service.consume_browser_pairing(pair["code"], session2)

    def test_cannot_create_pairing_for_another_owner(self):
        owner2, _cookie2, session2 = self.service.ensure_web_session("", "web:local")
        with self.assertRaises(PermissionError):
            self.service.create_browser_pairing(self.owner1, session2)
        with self.assertRaises(PermissionError):
            self.service.create_browser_pairing(owner2, "bogus")
        self.assertEqual(self.service.owner_for_web_cookie(self.cookie1), self.owner1)

    def test_invalid_expired_and_wrong_codes_rejected(self):
        owner2, cookie2, session2 = self.service.ensure_web_session("", "web:local")
        pair = self.service.create_browser_pairing(self.owner1, self.session1)
        with self.assertRaises(ValueError):
            self.service.consume_browser_pairing("123", session2)
        with self.assertRaises(ValueError):
            self.service.consume_browser_pairing("0" * 32, session2)
        with self.service.storage.connection() as conn:
            conn.execute("UPDATE owner_browser_pairings SET expires_at=0")
        with self.assertRaises(ValueError):
            self.service.consume_browser_pairing(pair["code"], session2)
        self.assertEqual(self.service.owner_for_web_cookie(cookie2), owner2)

    def test_pairing_does_not_work_without_active_originating_session(self):
        _, cookie2, session2 = self.service.ensure_web_session("", "web:local")
        pair = self.service.create_browser_pairing(self.owner1, self.session1)
        with self.service.storage.connection() as conn:
            conn.execute("DELETE FROM owner_web_sessions WHERE session_hash=?", (self.session1,))
        with self.assertRaises(ValueError):
            self.service.consume_browser_pairing(pair["code"], session2)
        self.assertNotEqual(self.service.owner_for_web_cookie(cookie2), self.owner1)

    def test_provider_diagnostics_dont_enable_fake_oauth(self):
        with patch.dict("os.environ", {
            "LESHY_AUTH_PUBLIC_BASE_URL": "http://192.168.1.10:8080",
            "LESHY_GOOGLE_CLIENT_ID": "test-client-id",
        }):
            statuses = provider_status()
        for provider in statuses[:3]:
            self.assertFalse(provider["enabled"])
            self.assertTrue(provider["requires_public_https_domain"])
        self.assertTrue(statuses[-1]["enabled"])
        self.assertEqual(statuses[-1]["id"], "gravatar")
        self.assertFalse(is_public_https_domain("https://192.168.1.10"))
        self.assertFalse(is_public_https_domain("http://leshy.example.com"))
        self.assertFalse(is_public_https_domain("https://localhost"))
        self.assertTrue(is_public_https_domain("https://leshy.example.com"))


if __name__ == "__main__":
    unittest.main()
