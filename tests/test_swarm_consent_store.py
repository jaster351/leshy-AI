from tempfile import TemporaryDirectory
from pathlib import Path

import pytest

from leshy.storage import Storage
from leshy.swarm_consent_store import SwarmConsentStore
from leshy.swarm_resource_consent import ResourceScope, ResourceTask, authorize_resource_task

ACK = "РАЗРЕШАЮ ИСПОЛЬЗОВАНИЕ РЕСУРСОВ"
REV = "2026-11-11.8"


def limits(cpu=20):
    return {"cpu": cpu, "gpu": 0, "ram": 0, "storage": 0, "network": 0}


def test_explicit_consent_is_expiring_and_revocable():
    with TemporaryDirectory() as tmp:
        store = SwarmConsentStore(Storage(Path(tmp) / "state.sqlite3"))
        assert store.status()["consent_active"] is False
        result = store.grant(
            confirmation=ACK, agreement_revision=REV, scopes=["cpu"],
            limits=limits(), duration_seconds=3600,
        )
        assert result["consent_active"] is True
        assert result["resource_sharing_enabled"] is False
        assert result["external_jobs_accepted"] is False
        consent = store._read()
        assert authorize_resource_task(
            consent, ResourceTask(
                "authenticated_swarm_peer", ResourceScope.CPU, 10,
                peer_identity_verified=True,
            ), current_revision=REV,
        )[0] is True
        assert store.revoke()["consent_active"] is False
        assert authorize_resource_task(
            store._read(), ResourceTask(
                "authenticated_swarm_peer", ResourceScope.CPU, 10,
                peer_identity_verified=True,
            ), current_revision=REV,
        )[0] is False
        with store.storage.connection() as db:
            events = [row[0] for row in db.execute(
                "SELECT event_type FROM audit_log ORDER BY id"
            )]
        assert events == [
            "swarm.resource_consent.granted",
            "swarm.resource_consent.revoked",
        ]


@pytest.mark.parametrize("changes", [
    {"confirmation": ""},
    {"agreement_revision": "outdated"},
    {"scopes": ["cpu", "cpu"]},
    {"scopes": ["network"]},
    {"duration_seconds": 0},
    {"duration_seconds": 90_000},
    {"limits": limits(cpu=101)},
    {"limits": limits(cpu=0)},
])
def test_malformed_or_implicit_grant_is_rejected(changes):
    with TemporaryDirectory() as tmp:
        store = SwarmConsentStore(Storage(Path(tmp) / "state.sqlite3"))
        request = dict(
            confirmation=ACK, agreement_revision=REV,
            scopes=["cpu"], limits=limits(), duration_seconds=3600,
        )
        request.update(changes)
        with pytest.raises(ValueError):
            store.grant(**request)
        assert store.status()["consent_active"] is False
