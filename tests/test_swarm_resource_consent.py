"""Проверки отдельного разрешения на вычислительные ресурсы роя."""
from datetime import datetime, timedelta, timezone
import unittest

from leshy.swarm_resource_consent import (
    ResourceScope, ResourceTask, SwarmConsent, authorize_resource_task,
)

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def consent(**overrides):
    values = dict(
        local_owner_confirmed=True,
        enabled=True,
        agreement_revision="2026-11-11.8",
        granted_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=30),
        scopes=frozenset({ResourceScope.CPU}),
        max_cpu_percent=25,
    )
    values.update(overrides)
    return SwarmConsent(**values)


def decide(c, task=None, revision="2026-11-11.8"):
    return authorize_resource_task(
        c,
        task if task is not None else ResourceTask(
            "authenticated_swarm_peer", ResourceScope.CPU, 10, peer_identity_verified=True
        ),
        current_revision=revision, now=NOW,
    )


class SwarmResourceConsentTests(unittest.TestCase):
    def test_no_consent_denied(self):
        self.assertFalse(decide(None)[0])
        self.assertFalse(decide(consent(local_owner_confirmed=False))[0])

    def test_expired_and_changed_terms_denied(self):
        self.assertFalse(decide(consent(expires_at=NOW))[0])
        self.assertFalse(decide(consent(), revision="new-edition")[0])

    def test_untrusted_and_risky_tasks_denied(self):
        self.assertFalse(decide(
            consent(), ResourceTask("anonymous", ResourceScope.CPU, 10)
        )[0])
        for flags in (
            {"is_remote_os_action": True},
            {"is_sensitive_data_access": True},
            {"is_destructive": True},
            {"is_network_attack": True},
        ):
            with self.subTest(flags=flags):
                task = ResourceTask(
                    "authenticated_swarm_peer", ResourceScope.CPU, 10,
                    peer_identity_verified=True, **flags,
                )
                self.assertFalse(decide(consent(), task)[0])

    def test_resource_limits_enforced(self):
        self.assertFalse(decide(
            consent(), ResourceTask(
                "authenticated_swarm_peer", ResourceScope.CPU, 26, peer_identity_verified=True,
            ),
        )[0])
        self.assertFalse(decide(
            consent(), ResourceTask(
                "authenticated_swarm_peer", ResourceScope.GPU, 1, peer_identity_verified=True,
            ),
        )[0])
        self.assertTrue(decide(consent())[0])

    def test_peer_claim_is_not_proof_of_identity(self):
        task = ResourceTask("authenticated_swarm_peer", ResourceScope.CPU, 10)
        self.assertEqual(decide(consent(), task), (False, "peer_identity_not_verified"))


if __name__ == "__main__":
    unittest.main()
