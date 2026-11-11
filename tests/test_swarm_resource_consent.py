"""Regression examples for the optional swarm resource policy."""
from datetime import datetime, timedelta, timezone

from leshy.swarm_resource_consent import (
    ResourceScope, ResourceTask, SwarmConsent, authorize_resource_task,
)

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def consent(**overrides):
    values = dict(
        local_owner_confirmed=True,
        enabled=True,
        agreement_revision="2026-11-11.7",
        granted_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=30),
        scopes=frozenset({ResourceScope.CPU}),
        max_cpu_percent=25,
    )
    values.update(overrides)
    return SwarmConsent(**values)


def decide(c, task=None, revision="2026-11-11.7"):
    return authorize_resource_task(
        c, task or ResourceTask("authenticated_swarm_peer", ResourceScope.CPU, 10, peer_identity_verified=True),
        current_revision=revision, now=NOW,
    )


def test_no_consent_denied():
    assert decide(None)[0] is False
    assert decide(consent(local_owner_confirmed=False))[0] is False


def test_expired_and_changed_terms_denied():
    assert decide(consent(expires_at=NOW))[0] is False
    assert decide(consent(), revision="new-edition")[0] is False


def test_untrusted_and_risky_tasks_denied():
    assert decide(consent(), ResourceTask("anonymous", ResourceScope.CPU, 10))[0] is False
    for flags in (
        {"is_remote_os_action": True},
        {"is_sensitive_data_access": True},
        {"is_destructive": True},
        {"is_network_attack": True},
    ):
        assert decide(consent(), ResourceTask("authenticated_swarm_peer", ResourceScope.CPU, 10, peer_identity_verified=True, **flags))[0] is False


def test_resource_limits_enforced():
    assert decide(consent(), ResourceTask("authenticated_swarm_peer", ResourceScope.CPU, 26, peer_identity_verified=True))[0] is False
    assert decide(consent(), ResourceTask("authenticated_swarm_peer", ResourceScope.GPU, 1, peer_identity_verified=True))[0] is False
    assert decide(consent())[0] is True


def test_peer_claim_is_not_proof_of_identity():
    task = ResourceTask("authenticated_swarm_peer", ResourceScope.CPU, 10)
    assert decide(consent(), task) == (False, "peer_identity_not_verified")
