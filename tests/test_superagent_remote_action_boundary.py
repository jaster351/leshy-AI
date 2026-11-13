"""Regression coverage for the in-process SuperAgent SSH/RDP permission gate."""
from __future__ import annotations

import hashlib
import sqlite3
import time
import unittest
from unittest.mock import Mock

from leshy.superagent_remote_action_boundary import RemoteActionBoundary
from leshy.superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor


class _Storage:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            "CREATE TABLE owner_web_sessions (session_hash TEXT PRIMARY KEY,"
            "owner_id TEXT, expires_at REAL);"
            "CREATE TABLE owner_identity_links (provider TEXT, owner_id TEXT,"
            "provider_user_id TEXT);"
        )

    def connection(self):
        outer = self

        class _Context:
            def __enter__(self):
                return outer.db

            def __exit__(self, typ, value, tb):
                if typ is None:
                    outer.db.commit()

        return _Context()


class RemoteActionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.storage = _Storage()
        identity = Mock(storage=self.storage)
        identity.owner_for_web_cookie.side_effect = (
            lambda cookie: {"owner-cookie": "owner-a", "other-cookie": "owner-b"}.get(cookie, "")
        )
        with self.storage.connection() as db:
            for cookie, owner in (("owner-cookie", "owner-a"), ("other-cookie", "owner-b")):
                db.execute(
                    "INSERT INTO owner_web_sessions VALUES (?, ?, ?)",
                    (hashlib.sha256(cookie.encode()).hexdigest(), owner, time.time() + 300),
                )
                db.execute(
                    "INSERT INTO owner_identity_links VALUES (?, ?, ?)",
                    ("github", owner, owner + "-verified"),
                )
        self.policy = RemotePermissionPolicy(
            identity,
            enrollment_verifier=lambda owner, node, fingerprint, proof: proof == "proof-from-node",
        )
        self.actor = self.policy.actor_from_web_cookie("owner-cookie")
        self.policy.register_verified_node(
            self.actor, "node-1", "a" * 64, enrollment_proof="proof-from-node"
        )
        self.operation = Mock(return_value={"checked": True})
        self.kwargs = {
            "web_cookie": "owner-cookie", "node_id": "node-1",
            "observed_fingerprint": "a" * 64, "operation": self.operation,
            "action_digest": "f" * 64,
        }

    def tearDown(self):
        self.storage.db.close()

    def test_verified_owner_can_inspect_but_cannot_execute_without_approval(self):
        boundary = RemoteActionBoundary(self.policy)
        self.assertEqual(
            boundary.perform(capability="inspect", **self.kwargs), {"checked": True}
        )
        self.operation.assert_called_once()
        self.assertEqual(self.operation.call_args.args[0].node_id, "node-1")
        self.operation.reset_mock()
        for capability in ("execute", "update", "reboot", "rdp"):
            with self.assertRaises(PermissionError):
                boundary.perform(capability=capability, approval_receipt="claimed", action_digest="a" * 64, **self.kwargs)
        self.operation.assert_not_called()

    def test_unknown_host_key_and_other_owner_are_rejected(self):
        boundary = RemoteActionBoundary(self.policy)
        with self.assertRaises(PermissionError):
            boundary.perform(capability="inspect", **{**self.kwargs, "observed_fingerprint": "b" * 64})
        with self.assertRaises(PermissionError):
            boundary.perform(capability="inspect", **{**self.kwargs, "web_cookie": "other-cookie"})
        self.operation.assert_not_called()

    def test_revoked_and_expired_web_sessions_are_rejected(self):
        boundary = RemoteActionBoundary(self.policy)
        with self.storage.connection() as db:
            db.execute("UPDATE owner_web_sessions SET expires_at=0 WHERE owner_id='owner-a'")
        with self.assertRaises(PermissionError):
            boundary.perform(capability="inspect", **self.kwargs)
        self.operation.assert_not_called()

    def test_grant_of_inspect_never_grants_rdp_or_execute(self):
        other = self.policy.actor_from_web_cookie("other-cookie")
        self.policy.grant(self.actor, "node-1", "owner-b", "inspect")
        boundary = RemoteActionBoundary(self.policy, approval_verifier=lambda *_: True)
        boundary.perform(
            capability="inspect", **{**self.kwargs, "web_cookie": "other-cookie"}
        )
        self.operation.reset_mock()
        for capability in ("rdp", "execute"):
            with self.assertRaises(PermissionError):
                boundary.perform(
                    capability=capability, approval_receipt="valid", action_digest="a" * 64,
                    **{**self.kwargs, "web_cookie": "other-cookie"}
                )
        self.operation.assert_not_called()

    def test_approval_verifier_rejection_and_revoke_race(self):
        denied = RemoteActionBoundary(
            self.policy, approval_verifier=lambda actor, node, capability, receipt: False
        )
        with self.assertRaises(PermissionError):
            denied.perform(capability="rdp", approval_receipt="receipt", action_digest="a" * 64, **self.kwargs)
        self.operation.assert_not_called()

        def revoke_during_confirmation(actor: VerifiedActor, node: str, capability: str, receipt: str):
            with self.storage.connection() as db:
                db.execute("DELETE FROM owner_web_sessions WHERE owner_id=?", (actor.owner_id,))
            return True

        raced = RemoteActionBoundary(self.policy, approval_verifier=revoke_during_confirmation)
        with self.assertRaises(PermissionError):
            raced.perform(capability="execute", approval_receipt="receipt", **self.kwargs)
        self.operation.assert_not_called()

    def test_valid_server_approval_runs_exactly_one_operation(self):
        verified = Mock(return_value=True)
        boundary = RemoteActionBoundary(self.policy, approval_verifier=verified)
        result = boundary.perform(capability="execute", approval_receipt="receipt-123", action_digest="a" * 64, **self.kwargs)
        self.assertEqual(result, {"checked": True})
        self.assertEqual(self.operation.call_count, 1)
        verified.assert_called_once()
        self.assertEqual(verified.call_args.args[2:], ("execute", "f" * 64, "receipt-123"))

    def test_missing_or_invalid_action_digest_denies_mutation(self):
        boundary = RemoteActionBoundary(self.policy, approval_verifier=lambda *_: True)
        for digest in ("", "not-a-digest", "a" * 63, "z" * 64):
            with self.assertRaises(PermissionError):
                boundary.perform(
                    capability="execute",
                    approval_receipt="receipt-for-other-action",
                    **{**self.kwargs, "action_digest": digest}
                )
        self.operation.assert_not_called()

    def test_arbitrary_commands_or_claimed_actor_do_not_bypass_gate(self):
        boundary = RemoteActionBoundary(self.policy)
        for capability in ("*", "shell", "sudo", "powershell"):
            with self.assertRaises(PermissionError):
                boundary.perform(capability=capability, **self.kwargs)
        with self.assertRaises(PermissionError):
            boundary.perform(
                capability="inspect", **{**self.kwargs, "web_cookie": "owner-a"}
            )
        self.operation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
