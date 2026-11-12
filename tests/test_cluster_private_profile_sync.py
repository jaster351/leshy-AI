"""Paired owner profiles must not leak other accounts or private settings."""
from __future__ import annotations

from hashlib import sha256
import hmac
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest

from leshy.cluster_private_profile_sync import ClusterPrivateProfileSync, _safe_endpoint, _json_bytes
from leshy.distributed_cluster import ClusterIdentity
from leshy.owner_identity import OwnerIdentityService
from leshy.storage import Storage, utcnow


class FakeCluster:
    def __init__(self, path, node, endpoint):
        self.identity = ClusterIdentity(path)
        self.identity.node_id = node
        self.local_node = SimpleNamespace(endpoint=endpoint)
        self.discovery = SimpleNamespace(peers=lambda: [])
        self._peers = []

    def set_peers(self, *peers):
        self._peers = list(peers)
        self.discovery.peers = lambda: self._peers

    def headers(self, body):
        timestamp = str(int(time.time()))
        raw = _json_bytes(body)
        signature = hmac.new(
            self.identity.secret, timestamp.encode("ascii") + b"\n" + raw, sha256,
        ).hexdigest()
        return {
            "X-Leshy-Cluster-Id": self.identity.cluster_id,
            "X-Leshy-Cluster-Node": self.identity.node_id,
            "X-Leshy-Cluster-Time": timestamp,
            "X-Leshy-Cluster-Signature": signature,
        }

    def verify_request(self, body, headers):
        if headers.get("X-Leshy-Cluster-Id") != self.identity.cluster_id:
            raise PermissionError("foreign cluster")
        timestamp = str(headers.get("X-Leshy-Cluster-Time") or "")
        if abs(time.time() - int(timestamp)) > 90:
            raise PermissionError("old request")
        expected = hmac.new(
            self.identity.secret, timestamp.encode("ascii") + b"\n" + _json_bytes(body), sha256
        ).hexdigest()
        if not hmac.compare_digest(str(headers.get("X-Leshy-Cluster-Signature") or ""), expected):
            raise PermissionError("bad signature")


class ClusterPrivateProfileSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.a_path = base / "a"
        self.b_path = base / "b"
        self.a_path.mkdir()
        self.b_path.mkdir()
        self.a_storage = Storage(self.a_path / "db.sqlite3")
        self.b_storage = Storage(self.b_path / "db.sqlite3")
        self.a_identity = OwnerIdentityService(self.a_storage, self.a_path)
        self.b_identity = OwnerIdentityService(self.b_storage, self.b_path)
        self.owner_a = self.a_identity._new_owner("Owner")
        self.owner_b = self.b_identity._new_owner("Owner")
        self.other_a = self.a_identity._new_owner("Another user")
        self.cluster_a = FakeCluster(self.a_path, "node-a", "http://192.168.42.21:8765")
        self.cluster_b = FakeCluster(self.b_path, "node-b", "http://192.168.42.22:8765")
        self.cluster_b.identity.secret = self.cluster_a.identity.secret
        self.cluster_b.identity.cluster_id = self.cluster_a.identity.cluster_id
        self.cluster_a.set_peers(SimpleNamespace(node_id="node-b", endpoint=self.cluster_b.local_node.endpoint))
        self.cluster_b.set_peers(SimpleNamespace(node_id="node-a", endpoint=self.cluster_a.local_node.endpoint))
        self.a = ClusterPrivateProfileSync(
            SimpleNamespace(storage=self.a_storage, settings=SimpleNamespace(brain_dir=self.a_path)),
            self.cluster_a,
        )
        self.b = ClusterPrivateProfileSync(
            SimpleNamespace(storage=self.b_storage, settings=SimpleNamespace(brain_dir=self.b_path)),
            self.cluster_b,
        )

        def post_from_b(endpoint, route, body):
            self.assertEqual(endpoint, self.cluster_a.local_node.endpoint)
            headers = self.cluster_b.headers(body)
            if route.endswith("accept"):
                return self.a.accept(body, headers)
            if route.endswith("pull"):
                return self.a.receive("pull", body, headers)
            if route.endswith("push"):
                return self.a.receive("push", body, headers)
            self.fail("unexpected route")

        self.b._signed_post = post_from_b

    def pair(self):
        invite = self.a.invite(self.owner_a)
        result = self.b.join(
            self.owner_b, invite["endpoint"], invite["invite_id"], invite["code"]
        )
        self.assertEqual(result["pair_count"], 1)
        self.assertEqual(self.a.status(self.owner_a)["pair_count"], 1)
        return result["pairs"][0]["pair_id"]

    def test_pairing_requires_valid_high_entropy_invitation(self):
        invite = self.a.invite(self.owner_a)
        with self.assertRaises(PermissionError):
            self.b.join(self.owner_b, invite["endpoint"], invite["invite_id"], "x" * 43)
        self.assertEqual(self.a.status(self.owner_a)["pair_count"], 0)
        self.assertEqual(self.b.status(self.owner_b)["pair_count"], 0)
        self.b.join(self.owner_b, invite["endpoint"], invite["invite_id"], invite["code"])
        with self.assertRaises(PermissionError):
            self.b.join(self.owner_b, invite["endpoint"], invite["invite_id"], invite["code"])

    def test_unpaired_local_owner_cannot_read_another_users_status(self):
        with self.assertRaises(PermissionError):
            self.a.status("victim-uuid")
        self.assertEqual(self.a.status(self.other_a)["pair_count"], 0)

    def test_safe_preferences_sync_both_ways_without_secret_or_other_owner(self):
        pair_id = self.pair()
        now = utcnow()
        with self.a_storage.connection() as conn:
            conn.executemany(
                "INSERT INTO user_preferences(owner_id,key,value_json,updated_at) VALUES(?,?,?,?)",
                [
                    (self.owner_a, "ui.theme", '"dark"', now),
                    (self.owner_a, "cloud.token", '"NEVER-COPY"', now),
                    (self.other_a, "ui.theme", '"other-user"', now),
                ],
            )
        self.b.sync_once(self.owner_b, pair_id)
        with self.b_storage.connection() as conn:
            rows = conn.execute("SELECT key,value_json FROM user_preferences WHERE owner_id=?",
                                (self.owner_b,)).fetchall()
        values = {row["key"]: row["value_json"] for row in rows}
        self.assertEqual(values, {"ui.theme": '"dark"'})
        with self.b_storage.connection() as conn:
            conn.execute(
                "UPDATE user_preferences SET value_json=?, updated_at=? WHERE owner_id=? AND key=?",
                ('"light"', "2099-01-01T00:00:00+00:00", self.owner_b, "ui.theme"),
            )
        # Future clock timestamps are rejected rather than being spread to peers.
        self.b.sync_once(self.owner_b, pair_id)
        with self.a_storage.connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM user_preferences WHERE owner_id=? AND key='ui.theme'",
                (self.owner_a,),
            ).fetchone()
        self.assertEqual(row["value_json"], '"dark"')

    def test_encrypted_profile_name_sync_uses_local_owner_mapping(self):
        pair_id = self.pair()
        with self.a_storage.connection() as conn:
            conn.execute(
                "UPDATE owner_profiles SET display_name=?,updated_at=? WHERE id=?",
                ("Мой первый компьютер", utcnow(), self.owner_a),
            )
        self.b.sync_once(self.owner_b, pair_id)
        with self.b_storage.connection() as conn:
            owner = conn.execute(
                "SELECT display_name FROM owner_profiles WHERE id=?", (self.owner_b,)
            ).fetchone()
        self.assertEqual(owner["display_name"], "Мой первый компьютер")
        with self.a_storage.connection() as conn:
            unrelated = conn.execute(
                "SELECT display_name FROM owner_profiles WHERE id=?", (self.other_a,)
            ).fetchone()
        self.assertEqual(unrelated["display_name"], "Another user")

    def test_revocation_immediately_disables_remote_owner_data(self):
        pair_id = self.pair()
        self.a.revoke(self.owner_a, pair_id)
        with self.assertRaises(PermissionError):
            self.b.sync_once(self.owner_b, pair_id)
        with self.assertRaises(PermissionError):
            self.a._pair_row(pair_id)

    def test_non_lan_endpoints_are_rejected(self):
        for endpoint in (
            "https://example.com:8765", "http://127.0.0.1:8765",
            "http://169.254.1.1:8765", "http://192.168.1.2:8765/internal",
            "https://user:password@192.168.1.2:8765",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    _safe_endpoint(endpoint)
        self.assertEqual(_safe_endpoint("http://192.168.1.2:8765"),
                         "http://192.168.1.2:8765")


if __name__ == "__main__":
    unittest.main()
