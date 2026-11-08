from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from types import SimpleNamespace
import unittest

from leshy.community_identity import CommunityIdentity
from leshy.swarm_membership import SwarmMembership, bootstrap_endpoint
from leshy.swarm_rendezvous import RendezvousRegistry


class _Storage:
    def __init__(self, path: Path):
        self.path = path
        self.audits = []
        with self.connection() as db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def audit(self, kind, fields):
        self.audits.append((kind, fields))


class SwarmMembershipTests(unittest.TestCase):
    def test_public_bootstrap_requires_https(self):
        self.assertEqual(
            bootstrap_endpoint("https://swarm.example.org"),
            "https://swarm.example.org/v1/swarm/hello",
        )
        for url in (
            "http://swarm.example.org",
            "http://localhost:38865",
            "https://user:password@swarm.example.org",
            "https://swarm.example.org/?token=secret",
            "https://swarm.example.org#fragment",
            "https://swarm.example.org/malicious",
            "https://",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    bootstrap_endpoint(url)
        self.assertEqual(
            bootstrap_endpoint("http://127.0.0.1:38865", allow_local_http=True),
            "http://127.0.0.1:38865/v1/swarm/hello",
        )

    def test_enabled_by_default_but_no_resource_sharing(self):
        with TemporaryDirectory() as tmp:
            app = SimpleNamespace(
                settings=SimpleNamespace(brain_dir=Path(tmp)),
                storage=_Storage(Path(tmp) / "state.sqlite3"),
            )
            from unittest.mock import patch
            with patch.dict("os.environ", {"LESHY_SWARM_BOOTSTRAP_URL": ""}):
                swarm = SwarmMembership(app)
            self.assertTrue(swarm.enabled())
            self.assertFalse(swarm.status()["bootstrap_configured"])
            self.assertFalse(swarm.status()["resource_sharing_enabled"])
            swarm.set_enabled(False)
            self.assertFalse(swarm.enabled())
            self.assertEqual(swarm.status()["state"], "disabled")
            swarm.set_enabled(True)
            self.assertTrue(swarm.enabled())
            self.assertEqual([x[0] for x in app.storage.audits], [
                "swarm.auto_join.changed", "swarm.auto_join.changed",
            ])

    def test_registry_verifies_signed_identity_and_replay(self):
        with TemporaryDirectory() as tmp:
            ident = CommunityIdentity(Path(tmp), "swarm:node")
            reg = RendezvousRegistry(Path(tmp) / "registry.sqlite3")
            content = {
                "swarm_protocol": 1, "version": "1.10.92",
                "resource_sharing_enabled": False,
                "remote_execution_enabled": False,
            }
            hello = ident.sign("cluster.hello", content)
            answer = reg.register(hello)
            self.assertTrue(answer["accepted"])
            self.assertEqual(answer["peer_count"], 1)
            self.assertEqual(reg.count(), 1)
            with self.assertRaises(ValueError):
                reg.register(hello)
            hello2 = ident.sign("cluster.hello", content)
            hello2["payload"]["resource_sharing_enabled"] = True
            # Tampering breaks Ed25519 signature before registration permissions.
            with self.assertRaises(ValueError):
                reg.register(hello2)

    def test_registry_rejects_remote_execution_even_if_signed(self):
        with TemporaryDirectory() as tmp:
            ident = CommunityIdentity(Path(tmp), "swarm:node")
            reg = RendezvousRegistry(Path(tmp) / "registry.sqlite3")
            signed = ident.sign("cluster.hello", {
                "swarm_protocol": 1, "version": "1.10.92",
                "resource_sharing_enabled": True,
                "remote_execution_enabled": True,
            })
            with self.assertRaises(ValueError):
                reg.register(signed)


if __name__ == "__main__":
    unittest.main()
