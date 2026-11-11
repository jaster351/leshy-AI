"""Проверки хранения, истечения и отзыва явного согласия владельца."""
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from leshy.storage import Storage
from leshy.swarm_consent_store import SwarmConsentStore
from leshy.swarm_resource_consent import ResourceScope, ResourceTask, authorize_resource_task

ACK = "РАЗРЕШАЮ ИСПОЛЬЗОВАНИЕ РЕСУРСОВ"
REV = "2026-11-11.8"


def limits(cpu=20):
    return {"cpu": cpu, "gpu": 0, "ram": 0, "storage": 0, "network": 0}


class SwarmConsentStoreTests(unittest.TestCase):
    def test_explicit_consent_is_expiring_and_revocable(self):
        with TemporaryDirectory() as tmp:
            store = SwarmConsentStore(Storage(Path(tmp) / "state.sqlite3"))
            self.assertFalse(store.status()["consent_active"])
            status = store.grant(
                confirmation=ACK, agreement_revision=REV, scopes=["cpu"],
                limits=limits(), duration_seconds=3600,
            )
            self.assertTrue(status["consent_active"])
            self.assertFalse(status["resource_sharing_enabled"])
            self.assertFalse(status["external_jobs_accepted"])
            consent = store._read()
            self.assertTrue(authorize_resource_task(
                consent, ResourceTask(
                    "authenticated_swarm_peer", ResourceScope.CPU, 10,
                    peer_identity_verified=True,
                ), current_revision=REV,
            )[0])
            self.assertFalse(store.revoke()["consent_active"])
            self.assertFalse(authorize_resource_task(
                store._read(), ResourceTask(
                    "authenticated_swarm_peer", ResourceScope.CPU, 10,
                    peer_identity_verified=True,
                ), current_revision=REV,
            )[0])
            with store.storage.connection() as db:
                events = [row[0] for row in db.execute(
                    "SELECT event_type FROM audit_log ORDER BY id"
                )]
            self.assertEqual(events, [
                "swarm.resource_consent.granted",
                "swarm.resource_consent.revoked",
            ])

    def test_malformed_or_implicit_grants_rejected(self):
        bad = (
            {"confirmation": ""},
            {"agreement_revision": "outdated"},
            {"scopes": ["cpu", "cpu"]},
            {"scopes": ["network"]},
            {"duration_seconds": 0},
            {"duration_seconds": 90_000},
            {"limits": limits(cpu=101)},
            {"limits": limits(cpu=0)},
        )
        with TemporaryDirectory() as tmp:
            store = SwarmConsentStore(Storage(Path(tmp) / "state.sqlite3"))
            for changes in bad:
                with self.subTest(changes=changes):
                    request = dict(
                        confirmation=ACK, agreement_revision=REV,
                        scopes=["cpu"], limits=limits(), duration_seconds=3600,
                    )
                    request.update(changes)
                    with self.assertRaises(ValueError):
                        store.grant(**request)
                    self.assertFalse(store.status()["consent_active"])


if __name__ == "__main__":
    unittest.main()
