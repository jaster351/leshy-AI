"""Owner-only remote-access inventory and revocation, without network access."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from leshy.superagent_remote_access_dashboard import (
    authorized_remote_context, list_remote_access, revoke_remote_grant,
)
from leshy.superagent_remote_permissions import RemotePermissionPolicy
from test_superagent_remote_permissions import Storage


class RemoteAccessConsoleTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = Mock(storage=self.storage, cookie_name="leshy")
        self.identity.owner_for_web_cookie.side_effect = lambda token: {
            "owner-cookie": "owner", "other-cookie": "other",
            "anonymous-cookie": "anonymous",
        }.get(token, "")
        with self.storage.connection() as conn:
            for actor, cookie in (
                ("owner", "owner-cookie"),
                ("other", "other-cookie"),
                ("anonymous", "anonymous-cookie"),
            ):
                conn.execute("INSERT INTO owner_web_sessions VALUES(?,?,?)", (
                    hashlib.sha256(cookie.encode()).hexdigest(), actor, time.time() + 3600
                ))
            for owner in ("owner", "other"):
                conn.execute("INSERT INTO owner_identity_links VALUES(?,?,?)", (
                    "github", owner, owner + "-github"
                ))
        self.app = SimpleNamespace(owner_identity=self.identity)
        self.policy, self.owner = authorized_remote_context(
            self.app, "owner", "owner-cookie")
        self.node = "192.168.1.9"
        self.policy = RemotePermissionPolicy(
            self.identity,
            enrollment_verifier=lambda owner, node, fp, proof: proof == "valid-proof"
        )
        self.policy.register_verified_node(
            self.owner, self.node, "a" * 64, enrollment_proof="valid-proof")
        self.policy.grant(self.owner, self.node, "other", "inspect")

    def test_owner_sees_only_owned_nodes_without_host_keys(self):
        view = list_remote_access(self.policy, self.owner, remote_admin_enabled=False)
        self.assertFalse(view["remote_admin_enabled"])
        self.assertFalse(view["transport_ready"])
        self.assertEqual([x["node_id"] for x in view["nodes"]], [self.node])
        self.assertEqual(len(view["active_grants"]), 1)
        self.assertEqual(view["active_grants"][0]["capability"], "inspect")
        self.assertNotIn("a" * 64, json.dumps(view))
        self.assertNotIn("owner_id", json.dumps(view))

    def test_other_owner_cannot_view_or_revoke(self):
        other_policy, other = authorized_remote_context(
            self.app, "other", "other-cookie")
        view = list_remote_access(other_policy, other, remote_admin_enabled=True)
        self.assertEqual(view["nodes"], [])
        self.assertEqual(view["active_grants"], [])
        with self.assertRaises(PermissionError):
            revoke_remote_grant(other_policy, other, {
                "node_id": self.node, "grantee_id": "other", "capability": "inspect"
            })
        self.assertEqual(len(list_remote_access(self.policy, self.owner,
            remote_admin_enabled=False)["active_grants"]), 1)

    def test_revocation_exact_and_idempotent(self):
        request = {"node_id": self.node, "grantee_id": "other",
                   "capability": "inspect"}
        self.assertTrue(revoke_remote_grant(self.policy, self.owner, request)["revoked"])
        self.assertEqual(list_remote_access(self.policy, self.owner,
            remote_admin_enabled=False)["active_grants"], [])
        self.assertTrue(revoke_remote_grant(self.policy, self.owner, request)["revoked"])

    def test_session_and_provider_are_required(self):
        with self.assertRaises(PermissionError):
            authorized_remote_context(self.app, "owner", "")
        with self.assertRaises(PermissionError):
            authorized_remote_context(self.app, "other", "owner-cookie")
        with self.assertRaises(PermissionError):
            authorized_remote_context(self.app, "anonymous", "anonymous-cookie")
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM owner_web_sessions WHERE owner_id='owner'")
        with self.assertRaises(PermissionError):
            list_remote_access(self.policy, self.owner, remote_admin_enabled=True)

    def test_no_registration_or_grant_through_console(self):
        invalid = (
            {},
            {"node_id": self.node, "grantee_id": "other",
             "capability": "inspect", "grant": True},
            {"node_id": self.node, "grantee_id": "other"},
            {"node_id": "", "grantee_id": "other", "capability": "inspect"},
            {"node_id": self.node, "grantee_id": "other", "capability": "*"},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(
                (ValueError, PermissionError)
            ):
                revoke_remote_grant(self.policy, self.owner, payload)

    def test_web_controls_exist_without_remote_command_buttons(self):
        js = (Path(__file__).resolve().parents[1] /
              "web" / "superagent-dashboard.js").read_text(encoding="utf-8")
        for label in ("Удалённые компьютеры и разрешения", "Отозвать это разрешение",
                      "/v1/superagent/remote-access/revoke",
                      "Новый компьютер не добавляется по одному IP"):
            self.assertIn(label, js)
        self.assertNotIn("action:'remote_shell'", js)

    def test_expired_grant_excluded_without_disabling_revocation(self):
        with self.storage.connection() as conn:
            conn.execute("UPDATE superagent_remote_permissions SET expires_at=0")
        view = list_remote_access(self.policy, self.owner, remote_admin_enabled=True)
        self.assertEqual(view["active_grants"], [])
        self.assertEqual(len(view["nodes"]), 1)


if __name__ == "__main__":
    unittest.main()
