from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import time
import unittest

from leshy.community_identity import CommunityIdentity
from leshy.swarm_peer_exchange import SwarmPeerCache, public_peer_url, valid_peer_rows
from leshy.swarm_rendezvous import RendezvousRegistry


def hello(identity, *, endpoint=""):
    payload = {
        "swarm_protocol": 1,
        "version": "1.10.95",
        "resource_sharing_enabled": False,
        "remote_execution_enabled": False,
    }
    if endpoint:
        payload["public_endpoint"] = endpoint
    return identity.sign("cluster.hello", payload)


class SwarmPeerExchangeTests(unittest.TestCase):
    def test_only_voluntary_global_https_endpoints(self):
        self.assertEqual(public_peer_url("https://node1.example.org"), "https://node1.example.org")
        for raw in (
            "http://node1.example.org", "https://localhost:38865",
            "https://127.0.0.1:38865", "https://192.168.1.8",
            "https://10.0.0.3", "https://node.local",
            "https://raw.githubusercontent.com", "https://github.com",
            "https://user:password@node1.example.org",
            "https://node1.example.org/?token=1", "https://node1.example.org/admin",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    public_peer_url(raw)

    def test_bounded_untrusted_peer_rows(self):
        row = {"key_id": "a" * 64, "url": "https://node1.example.org"}
        self.assertEqual(valid_peer_rows([row]), (row,))
        for invalid in (
            [{"key_id": "wrong", "url": "https://node1.example.org"}],
            [{"key_id": "a" * 64, "url": "http://127.0.0.1"}],
            [{"key_id": "a" * 64, "url": "https://node1.example.org", "token": "secret"}],
            [row] * 17,
        ):
            with self.assertRaises(ValueError):
                valid_peer_rows(invalid)

    def test_peer_cache_survives_restart_without_github(self):
        with TemporaryDirectory() as tmp:
            first = SwarmPeerCache(tmp)
            own = "a" * 64
            peer = {"key_id": "b" * 64, "url": "https://node2.example.org"}
            first.learn([peer, {"key_id": own, "url": "https://self.example.org"}], own_key_id=own)
            self.assertEqual(first.urls(), ("https://node2.example.org",))
            second = SwarmPeerCache(tmp)
            self.assertEqual(second.load(), ("https://node2.example.org",))
            self.assertTrue((Path(tmp) / "cluster" / "swarm-peers.json").is_file())

    def test_expired_peers_are_discarded(self):
        with TemporaryDirectory() as tmp:
            cache = SwarmPeerCache(tmp)
            cache.learn([{"key_id": "b" * 64, "url": "https://node2.example.org"}])
            with cache._lock:
                cache._peers["b" * 64] = ("https://node2.example.org", int(time.time()) - 1)
            self.assertEqual(cache.urls(), ())

    def test_signed_hello_gossip_without_personal_information(self):
        with TemporaryDirectory() as tmp:
            a = CommunityIdentity(Path(tmp) / "a", "swarm:node")
            b = CommunityIdentity(Path(tmp) / "b", "swarm:node")
            out_only = CommunityIdentity(Path(tmp) / "c", "swarm:node")
            registry = RendezvousRegistry(Path(tmp) / "registry.sqlite3")
            first = registry.register(hello(a, endpoint="https://volunteer.example.org"))
            self.assertEqual(first["peers"], [])
            second = registry.register(hello(b))
            self.assertEqual(
                second["peers"],
                [{"key_id": a.key_id, "url": "https://volunteer.example.org"}],
            )
            third = registry.register(hello(out_only, endpoint="https://volunteer2.example.org"))
            self.assertEqual(third["peers"], [
                {"key_id": a.key_id, "url": "https://volunteer.example.org"},
            ])
            again = registry.register(hello(a, endpoint="https://volunteer.example.org"))
            self.assertEqual(
                again["peers"],
                [{"key_id": out_only.key_id, "url": "https://volunteer2.example.org"}],
            )
            self.assertEqual(registry.count(), 3)
            with sqlite3.connect(Path(tmp) / "registry.sqlite3") as db:
                rows = db.execute("SELECT public_endpoint FROM peers").fetchall()
            self.assertEqual(sorted(value[0] for value in rows), [
                "", "https://volunteer.example.org", "https://volunteer2.example.org",
            ])

    def test_outbound_identity_cannot_advertise_private_or_http_endpoint(self):
        with TemporaryDirectory() as tmp:
            a = CommunityIdentity(Path(tmp) / "a", "swarm:node")
            registry = RendezvousRegistry(Path(tmp) / "registry.sqlite3")
            for endpoint in ("http://example.org", "https://localhost", "https://10.1.2.3"):
                with self.subTest(endpoint=endpoint):
                    with self.assertRaises(ValueError):
                        registry.register(hello(a, endpoint=endpoint))
            self.assertEqual(registry.count(), 0)

    def test_gossip_migration_existing_registry_keeps_old_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE peers("
                           "key_id TEXT PRIMARY KEY,last_seen INTEGER NOT NULL,"
                           "protocol INTEGER NOT NULL)")
                db.execute(
                    "INSERT INTO peers(key_id,last_seen,protocol) VALUES(?,?,1)",
                    ("a" * 64, int(time.time())),
                )
            registry = RendezvousRegistry(path)
            self.assertEqual(registry.count(), 1)
            with sqlite3.connect(path) as db:
                names = {col[1] for col in db.execute("PRAGMA table_info(peers)")}
            self.assertIn("public_endpoint", names)


if __name__ == "__main__":
    unittest.main()
