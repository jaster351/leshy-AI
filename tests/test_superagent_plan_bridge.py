"""Durable step plans are ticket-bound and reject untrusted owner transitions."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from leshy.superagent_plan_bridge import SuperAgentPlanBridge, PlanError


class FakeService:
    def __init__(self, path):
        self.storage = SimpleNamespace(path=path)
        self.tickets = {"SA-ticket1": {"id": "SA-ticket1", "owner_id": "web:alice", "state": "queued"}}

    def get(self, ticket_id, *, owner_id):
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise KeyError("Ticket not found")
        if ticket["owner_id"] != owner_id:
            raise PermissionError("Wrong owner")
        return dict(ticket)


class PlanBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = FakeService(Path(self.tmp.name) / "brain.sqlite")
        self.bridge = SuperAgentPlanBridge(self.service)
        self.steps = [
            {"id": "analyse", "title": "Read repository"},
            {"id": "implement", "title": "Write patch", "depends_on": ["analyse"]},
        ]

    def test_create_and_claim_dependency_after_restart(self):
        snap = self.bridge.create("SA-ticket1", "web:alice", self.steps)
        self.assertEqual(snap["state"], "active")
        first = self.bridge.claim("SA-ticket1", "web:alice", limit=8)
        self.assertEqual([x["id"] for x in first], ["analyse"])
        self.assertTrue(self.bridge.complete("SA-ticket1", "web:alice", "analyse", first[0]["token"], success=True, output="reviewed"))
        restarted = SuperAgentPlanBridge(self.service)
        later = restarted.claim("SA-ticket1", "web:alice")
        self.assertEqual([x["id"] for x in later], ["implement"])
        restarted.complete("SA-ticket1", "web:alice", "implement", later[0]["token"], success=True, output="done")
        self.assertEqual(restarted.snapshot("SA-ticket1", "web:alice")["state"], "completed")

    def test_owner_isolation_including_control_and_completion(self):
        self.bridge.create("SA-ticket1", "web:alice", self.steps)
        for call in (
            lambda: self.bridge.snapshot("SA-ticket1", "web:mallory"),
            lambda: self.bridge.control("SA-ticket1", "web:mallory", "cancel"),
            lambda: self.bridge.claim("SA-ticket1", "web:mallory"),
            lambda: self.bridge.complete("SA-ticket1", "web:mallory", "analyse", "fake", success=True),
        ):
            with self.assertRaises(PermissionError):
                call()

    def test_duplicate_plan_is_rejected_with_clear_error(self):
        self.bridge.create("SA-ticket1", "web:alice", self.steps)
        with self.assertRaisesRegex(PlanError, "already exists"):
            self.bridge.create("SA-ticket1", "web:alice", self.steps)

    def test_terminal_ticket_blocks_new_claims_and_completion(self):
        self.bridge.create("SA-ticket1", "web:alice", self.steps)
        claim = self.bridge.claim("SA-ticket1", "web:alice")[0]
        self.service.tickets["SA-ticket1"]["state"] = "stopped"
        self.assertEqual(self.bridge.claim("SA-ticket1", "web:alice"), [])
        self.assertFalse(self.bridge.complete("SA-ticket1", "web:alice", "analyse", claim["token"], success=True))
        self.assertEqual(self.bridge.snapshot("SA-ticket1", "web:alice")["state"], "cancelled")

    def test_stop_racing_with_claim_revokes_lease(self):
        self.bridge.create("SA-ticket1", "web:alice", self.steps)
        original = self.bridge.store.claim
        def change_state(*args, **kwargs):
            items = original(*args, **kwargs)
            self.service.tickets["SA-ticket1"]["state"] = "stopped"
            return items
        self.bridge.store.claim = change_state
        self.assertEqual(self.bridge.claim("SA-ticket1", "web:alice"), [])
        self.assertEqual(self.bridge.snapshot("SA-ticket1", "web:alice")["state"], "cancelled")

    def test_terminal_ticket_rejects_new_plan_and_resume(self):
        self.service.tickets["SA-ticket1"]["state"] = "ready"
        with self.assertRaises(PlanError): self.bridge.create("SA-ticket1", "web:alice", self.steps)
        self.service.tickets["SA-ticket1"]["state"] = "queued"
        self.bridge.create("SA-ticket1", "web:alice", self.steps)
        self.bridge.control("SA-ticket1", "web:alice", "pause")
        self.service.tickets["SA-ticket1"]["state"] = "ready"
        with self.assertRaises(PlanError): self.bridge.control("SA-ticket1", "web:alice", "resume")

    def test_plan_create_requires_existing_ticket_and_does_not_run_commands(self):
        with self.assertRaises(KeyError): self.bridge.create("SA-missing", "web:alice", self.steps)
        with self.assertRaises(PlanError): self.bridge.create("not-a-ticket", "web:alice", self.steps)
        self.assertEqual(self.bridge.create("SA-ticket1", "web:alice", self.steps)["steps"][0]["state"], "queued")


    def test_owner_bound_heartbeat_renews_lease_and_rejects_stale_tokens(self):
        self.bridge.create("SA-ticket1", "web:alice", self.steps)
        claim = self.bridge.claim("SA-ticket1", "web:alice", lease_seconds=90)[0]
        self.assertTrue(self.bridge.heartbeat(
            "SA-ticket1", "web:alice", claim["id"], claim["token"]))
        self.assertFalse(self.bridge.heartbeat(
            "SA-ticket1", "web:alice", claim["id"], "stale-token"))
        with self.assertRaises(PermissionError):
            self.bridge.heartbeat("SA-ticket1", "web:mallory", claim["id"], claim["token"])

    def test_stop_racing_with_lease_renewal_revokes_plan(self):
        self.bridge.create("SA-ticket1", "web:alice", self.steps)
        claim = self.bridge.claim("SA-ticket1", "web:alice")[0]
        original = self.bridge.store.heartbeat
        def stop_during_heartbeat(*args, **kwargs):
            renewed = original(*args, **kwargs)
            self.service.tickets["SA-ticket1"]["state"] = "stopped"
            return renewed
        self.bridge.store.heartbeat = stop_during_heartbeat
        self.assertFalse(self.bridge.heartbeat(
            "SA-ticket1", "web:alice", claim["id"], claim["token"]))
        self.assertEqual(self.bridge.snapshot("SA-ticket1", "web:alice")["state"], "cancelled")
        self.assertFalse(self.bridge.complete(
            "SA-ticket1", "web:alice", claim["id"], claim["token"],
            success=True, output="late answer"))



if __name__ == "__main__": unittest.main()
