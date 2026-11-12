"""Remote access must fail closed until provider login and host enrollment."""
from __future__ import annotations

import sqlite3
import time
import unittest
from unittest.mock import Mock

from leshy.superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor


class Storage:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE owner_web_sessions(session_hash TEXT PRIMARY KEY,owner_id TEXT,expires_at REAL);
        CREATE TABLE owner_identity_links(provider TEXT,owner_id TEXT,provider_user_id TEXT);
        """)
    def connection(self):
        class Context:
            def __enter__(self_inner):
                return self.conn
            def __exit__(self_inner, exc_type, exc, tb):
                if exc_type is None:
                    self.conn.commit()
        return Context()


class RemotePolicyTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = Mock(storage=self.storage)
        self.cookies = {"owner": "session-a", "other": "session-b", "anonymous": "session-c"}
        self.identity.owner_for_web_cookie.side_effect = lambda cookie: {
            "session-a": "owner", "session-b": "other", "session-c": "anonymous"
        }.get(cookie, "")
        import hashlib
        with self.storage.connection() as conn:
            for owner, cookie in self.cookies.items():
                conn.execute(
                    "INSERT INTO owner_web_sessions VALUES(?,?,?)",
                    (hashlib.sha256(cookie.encode()).hexdigest(), owner, time.time() + 120),
                )
            for owner in ("owner", "other"):
                conn.execute(
                    "INSERT INTO owner_identity_links VALUES(?,?,?)",
                    ("github", owner, owner + "-github-id"),
                )
        self.policy = RemotePermissionPolicy(self.identity, enrollment_verifier=lambda owner, node, fp, proof: proof == 'signed-attestation')
        self.owner = self.policy.actor_from_web_cookie("session-a")
        self.other = self.policy.actor_from_web_cookie("session-b")

    def test_password_or_ip_does_not_authorize(self):
        with self.assertRaises(PermissionError):
            self.policy.require(self.owner, "192.168.1.5", "update", observed_fingerprint="x"*32)
        with self.assertRaises(PermissionError):
            self.policy.register_verified_node(self.owner, "server1", "x"*32)

    def test_anonymous_browser_is_not_admin(self):
        anonymous = self.policy.actor_from_web_cookie("session-c")
        with self.assertRaises(PermissionError):
            self.policy.register_verified_node(anonymous, "server1", "x"*32, enrollment_proof='signed-attestation')

    def test_other_user_denied_until_explicit_grant(self):
        self.policy.register_verified_node(self.owner, "server1", "x"*32, enrollment_proof='signed-attestation')
        with self.assertRaises(PermissionError):
            self.policy.require(self.other, "server1", "inspect", observed_fingerprint="x"*32)
        self.policy.grant(self.owner, "server1", "other", "inspect")
        self.policy.require(self.other, "server1", "inspect", observed_fingerprint="x"*32)
        with self.assertRaises(PermissionError):
            self.policy.require(self.other, "server1", "execute", observed_fingerprint="x"*32)
        self.policy.revoke(self.owner, "server1", "other", "inspect")
        with self.assertRaises(PermissionError):
            self.policy.require(self.other, "server1", "inspect", observed_fingerprint="x"*32)

    def test_grantee_cannot_transfer_access(self):
        self.policy.register_verified_node(self.owner, "server1", "x"*32, enrollment_proof='signed-attestation')
        self.policy.grant(self.owner, "server1", "other", "inspect")
        with self.assertRaises(PermissionError):
            self.policy.grant(self.other, "server1", "someone", "update")
        with self.assertRaises(PermissionError):
            self.policy.register_verified_node(self.other, "server1", "x"*32, enrollment_proof='signed-attestation')

    def test_host_key_change_and_expiry_fail(self):
        self.policy.register_verified_node(self.owner, "server1", "x"*32, enrollment_proof='signed-attestation')
        with self.assertRaises(PermissionError):
            self.policy.require(self.owner, "server1", "update", observed_fingerprint="y"*32)
        self.policy.grant(self.owner, "server1", "other", "inspect", ttl_seconds=1)
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_remote_permissions SET expires_at=0")
        with self.assertRaises(PermissionError):
            self.policy.require(self.other, "server1", "inspect", observed_fingerprint="x"*32)

    def test_revoked_web_session_fails_even_with_cached_actor(self):
        self.policy.register_verified_node(self.owner, "server1", "x"*32, enrollment_proof='signed-attestation')
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM owner_web_sessions WHERE owner_id='owner'")
        with self.assertRaises(PermissionError):
            self.policy.require(self.owner, "server1", "inspect", observed_fingerprint="x"*32)

    def test_owner_id_and_telegram_text_cannot_authenticate(self):
        with self.assertRaises(PermissionError):
            self.policy.actor_from_web_cookie("")
        with self.assertRaises(PermissionError):
            self.policy.require(VerifiedActor("owner", "bogus", "web"), "server1",
                                "inspect", observed_fingerprint="x"*32)
        with self.assertRaises(PermissionError):
            self.policy.require(VerifiedActor("owner", "valid", "telegram"), "server1",
                                "inspect", observed_fingerprint="x"*32)

    def test_no_wildcard_capability(self):
        self.policy.register_verified_node(self.owner, "server1", "x"*32, enrollment_proof='signed-attestation')
        with self.assertRaises(PermissionError):
            self.policy.grant(self.owner, "server1", "other", "*")


if __name__ == "__main__":
    unittest.main()
