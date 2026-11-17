"""Коллективная проверка происхождения: без смены полномочий и доверия голосованию."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import time
import unittest

from leshy.advanced_modules_runtime import _advanced_kind
from leshy.cluster_orchestrator import ClusterNode
from leshy.creator_consensus import (
    CreatorConsensusService, creator_identity_digest, summarize_creator_votes,
)
from leshy.distributed_cluster import (
    ClusterIdentity, LanPeerDiscovery, _canonical, _hmac, _DISCOVERY_MAGIC,
)
from leshy.identity_runtime import CREATOR_FULL_NAME
from leshy.storage import Storage


class _Discovery:
    def __init__(self, votes=None):
        self.votes = votes or {}

    def creator_peer_votes(self):
        return dict(self.votes)


class CreatorConsensusTests(unittest.TestCase):
    def test_collective_command_is_explicit_and_origin_answer_remains_model_driven(self):
        self.assertEqual(_advanced_kind(None, "/creator_consensus"),
                         ("creator_consensus", ""))
        self.assertEqual(_advanced_kind(None, "проверь создателя у федерации"),
                         ("creator_consensus", ""))
        self.assertIsNone(_advanced_kind(None, "Кто твой создатель?"))

    def test_consistent_nodes_count_without_promoting_role(self):
        digest = creator_identity_digest()
        result = summarize_creator_votes(digest, {"peer-1": digest, "peer-2": digest})
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(result["matching_local"], 3)
        self.assertEqual(result["majority_count"], 3)
        self.assertFalse(result["proof_of_creator_identity"])
        self.assertFalse(result["authorizes_role_change"])

    def test_divergence_and_local_outlier_do_not_rewrite_code(self):
        local = creator_identity_digest()
        other = "a" * 64 if local != "a" * 64 else "b" * 64
        report = summarize_creator_votes(local, {"peer-1": other, "peer-2": other})
        self.assertEqual(report["status"], "local_outlier")
        self.assertEqual(report["majority_digest"], other)
        self.assertEqual(report["differing_peer_ids"], ["peer-1", "peer-2"])
        self.assertEqual(creator_identity_digest(), local)

    def test_ties_and_invalid_votes_do_not_define_a_majority(self):
        digest = creator_identity_digest()
        other = "b" * 64 if digest != "b" * 64 else "c" * 64
        result = summarize_creator_votes(digest, {
            "peer-1": other, "broken": "не отпечаток",
        })
        self.assertEqual(result["status"], "conflict_no_majority")
        self.assertIsNone(result["majority_digest"])
        self.assertEqual(result["total_observed_nodes"], 2)

    def test_cache_repairs_only_namespaced_informational_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "data.sqlite3")
            discovery = _Discovery()
            service = CreatorConsensusService(storage, discovery)
            with storage.connection() as conn:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?)",
                    ("creator.identity.informational.v1", json.dumps({"name": "Подделка"})),
                )
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?)",
                    ("some.owner.role", "unchanged"),
                )
            self.assertEqual(service.reconcile_informational_cache(), "repaired")
            with storage.connection() as conn:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key=?", ("creator.identity.informational.v1",),
                ).fetchone()
                other = conn.execute(
                    "SELECT value FROM meta WHERE key='some.owner.role'",
                ).fetchone()
            data = json.loads(row["value"])
            self.assertEqual(data["name"], CREATOR_FULL_NAME)
            self.assertEqual(data["digest"], creator_identity_digest())
            self.assertEqual(other["value"], "unchanged")
            self.assertEqual(service.reconcile_informational_cache(), "consistent")

    def test_signed_beacon_votes_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = ClusterIdentity(Path(tmp))
            discovery = LanPeerDiscovery(identity)
            digest = creator_identity_digest()
            node = ClusterNode(
                node_id="another-computer", endpoint="http://192.0.2.10:8765",
                cpu_threads=4, last_seen=time.time(),
            )
            payload = {
                "magic": _DISCOVERY_MAGIC,
                "cluster_id": identity.cluster_id,
                "node": asdict(node),
                "creator_digest": digest,
            }
            signature = _hmac(identity.secret, _canonical(payload))
            discovery._accept_packet(_canonical({**payload, "signature": signature}),
                                     "192.0.2.10")
            self.assertEqual(discovery.creator_peer_votes(), {"another-computer": digest})
            bad = {**payload, "creator_digest": "c" * 64, "signature": signature}
            discovery._accept_packet(_canonical(bad), "192.0.2.10")
            self.assertEqual(discovery.creator_peer_votes(), {"another-computer": digest})
            self.assertGreater(discovery.diagnostics()["counts"]["invalid_signature"], 0)

    def test_unregistered_or_stale_votes_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            discovery = LanPeerDiscovery(ClusterIdentity(Path(tmp)))
            discovery._creator_remote["ghost"] = {
                "digest": creator_identity_digest(), "seen": time.time(),
            }
            self.assertEqual(discovery.creator_peer_votes(), {})
            discovery._peers["ghost"] = ClusterNode(
                node_id="ghost", endpoint="http://192.0.2.11:8765",
                cpu_threads=2, last_seen=time.time() - 100,
            )
            self.assertEqual(discovery.creator_peer_votes(), {})


if __name__ == "__main__":
    unittest.main()
