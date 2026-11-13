"""Owner opt-in, migration, and automatic durable read-plan eligibility."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from leshy.durable_plan_store import DurablePlanStore, PlanError
from leshy.superagent import parse_superagent_intent
from leshy.superagent_plan_bridge import SuperAgentPlanBridge
from leshy.superagent_plan_commands import execute_plan_command
from leshy.superagent_plan_runner import execute_ready_plan_reads


class Service:
    def __init__(self, path):
        self.storage = SimpleNamespace(path=path)
        self.tickets = {"SA-123": {"owner_id": "web:alice", "state": "queued"}}
        self._wake = threading.Event()
        self.plans = SuperAgentPlanBridge(self)
        self._cognitive_reads = Reader()

    def get(self, ticket_id, *, owner_id):
        item = self.tickets[ticket_id]
        if item["owner_id"] != owner_id:
            raise PermissionError("Wrong owner")
        return dict(item)


class Reader:
    def __init__(self):
        self.calls = 0

    def execute(self, name, arguments, *, ticket_id):
        self.calls += 1
        return {"status": "success", "tool": name,
                "receipt_id": f"verified-{self.calls}", "result": {"version": "fake"}}


class AutoPlanTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.path = Path(root.name) / "LeshyBrain.sqlite"
        self.s = Service(self.path)

    def steps(self):
        return [
            {"id": "version", "title": "Find version", "tool": "runtime.version"},
            {"id": "manual", "title": "Unapproved file modification",
             "depends_on": ["version"]},
            {"id": "blocked", "title": "Needs manual review",
             "tool": "runtime.platform", "depends_on": ["manual"]},
        ]

    def test_default_disabled_and_owner_opt_in_survives_restart(self):
        self.s.plans.create("SA-123", "web:alice", self.steps())
        self.assertFalse(self.s.plans.snapshot("SA-123", "web:alice")["auto_run"])
        self.assertEqual(self.s.plans.store.auto_plans(), [])
        self.assertFalse(self.s.plans.store.auto_ready("SA-123", "web:alice"))
        intent = parse_superagent_intent("/agent plan auto SA-123")
        self.assertEqual(intent.action, "plan_auto")
        reply = execute_plan_command(self.s, intent.action, intent.ticket_id, "web:alice")
        self.assertIn("включён", reply)
        self.assertTrue(self.s._wake.is_set())
        fresh = SuperAgentPlanBridge(self.s)
        self.assertTrue(fresh.snapshot("SA-123", "web:alice")["auto_run"])
        self.assertTrue(fresh.store.auto_ready("SA-123", "web:alice"))
        self.assertEqual([x["id"] for x in fresh.store.auto_plans()], ["SA-123"])

    def test_auto_read_only_does_not_execute_manual_steps(self):
        self.s.plans.create("SA-123", "web:alice", self.steps())
        self.s.plans.set_auto("SA-123", "web:alice", True)
        summary = execute_ready_plan_reads(
            self.s, "SA-123", "web:alice", require_auto=True,
        )
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(self.s._cognitive_reads.calls, 1)
        self.assertFalse(self.s.plans.store.auto_ready("SA-123", "web:alice"))
        states = [x["state"] for x in self.s.plans.snapshot("SA-123", "web:alice")["steps"]]
        self.assertEqual(states, ["done", "queued", "queued"])

    def test_manual_toggle_prevents_auto_read_but_explicit_run_still_works(self):
        self.s.plans.create("SA-123", "web:alice", [
            {"id": "version", "title": "Find version", "tool": "runtime.version"},
        ])
        self.s.plans.set_auto("SA-123", "web:alice", True)
        intent = parse_superagent_intent("/agent plan manual SA-123")
        self.assertEqual(intent.action, "plan_manual")
        self.assertIn("выключен", execute_plan_command(
            self.s, intent.action, intent.ticket_id, "web:alice"))
        blocked = execute_ready_plan_reads(
            self.s, "SA-123", "web:alice", require_auto=True,
        )
        self.assertEqual(blocked["completed"], 0)
        self.assertEqual(self.s._cognitive_reads.calls, 0)
        manually_run = execute_ready_plan_reads(
            self.s, "SA-123", "web:alice", require_auto=False,
        )
        self.assertEqual(manually_run["completed"], 1)

    def test_wrong_owner_and_stopped_ticket_cannot_enable(self):
        self.s.plans.create("SA-123", "web:alice", self.steps())
        with self.assertRaises(PermissionError):
            self.s.plans.set_auto("SA-123", "web:other", True)
        self.s.tickets["SA-123"]["state"] = "stopped"
        with self.assertRaises(PlanError):
            self.s.plans.set_auto("SA-123", "web:alice", True)
        self.assertFalse(self.s.plans.snapshot("SA-123", "web:alice")["auto_run"])

    def test_pause_cancel_and_expired_lease(self):
        self.s.plans.create("SA-123", "web:alice", [
            {"id": "a", "title": "Read", "tool": "runtime.version"},
        ])
        self.s.plans.set_auto("SA-123", "web:alice", True)
        self.s.plans.control("SA-123", "web:alice", "pause")
        self.assertFalse(self.s.plans.store.auto_ready("SA-123", "web:alice"))
        self.s.plans.control("SA-123", "web:alice", "resume")
        claim = self.s.plans.store.claim(
            "SA-123", "web:alice", now=100, lease_seconds=5,
        )[0]
        self.assertTrue(self.s.plans.store.auto_ready(
            "SA-123", "web:alice", now=106))
        self.assertFalse(self.s.plans.store.complete(
            "SA-123", "web:alice", claim["id"], claim["token"],
            success=True, now=106))
        self.s.plans.control("SA-123", "web:alice", "cancel")
        self.assertFalse(self.s.plans.snapshot("SA-123", "web:alice")["auto_run"])
        self.assertFalse(self.s.plans.store.auto_ready("SA-123", "web:alice"))
        with self.assertRaises(PlanError):
            self.s.plans.set_auto("SA-123", "web:alice", True)

    def test_cancel_mid_read_cannot_confirm_lease(self):
        self.s.plans.create("SA-123", "web:alice", [
            {"id": "a", "title": "Read", "tool": "runtime.version"},
        ])
        self.s.plans.set_auto("SA-123", "web:alice", True)
        class CancellingReader:
            def execute(inner, *args, **kwargs):
                self.s.plans.control("SA-123", "web:alice", "cancel")
                return {"tool": "runtime.version", "status": "success",
                        "receipt_id": "late", "result": {"version": "mock"}}
        self.s._cognitive_reads = CancellingReader()
        result = execute_ready_plan_reads(
            self.s, "SA-123", "web:alice", require_auto=True,
        )
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["state"], "cancelled")

    def test_upgrade_old_schema_preserves_plan_and_auto_disabled(self):
        old = Path(self.path.parent) / "old.sqlite"
        with sqlite3.connect(old) as db:
            db.execute(
                "CREATE TABLE durable_plans ("
                "plan_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,"
                "state TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            db.execute(
                "INSERT INTO durable_plans VALUES ('SA-old','web:alice','active',0)"
            )
        store = DurablePlanStore(old)
        self.assertFalse(store.auto_plans())
        with sqlite3.connect(old) as db:
            row = db.execute(
                "SELECT owner_id,state,auto_run FROM durable_plans WHERE plan_id='SA-old'"
            ).fetchone()
        self.assertEqual(row, ("web:alice", "active", 0))


if __name__ == "__main__":
    unittest.main()
