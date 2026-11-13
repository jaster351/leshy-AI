"""Regressions for explicit owner-bound read-only DAG execution."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from leshy.superagent import parse_superagent_intent
from leshy.superagent_plan_bridge import SuperAgentPlanBridge
from leshy.superagent_plan_commands import execute_plan_command
from leshy.superagent_plan_runner import execute_ready_plan_reads
from leshy.verified_tool_receipts import VerifiedToolReceipts


class Reader:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.fail = False
        self.receipts = VerifiedToolReceipts()

    def execute(self, tool, arguments, *, ticket_id):
        with self.lock:
            self.calls.append(tool)
            index = len(self.calls)
        if self.fail:
            raise RuntimeError("Unavailable")
        result = {"count": 0, "items": []}
        receipt = self.receipts.record(tool, "success", result)
        return {"status": "success", "tool": tool, "receipt_id": receipt.receipt_id,
                "result": result}


class Service:
    def __init__(self, path):
        self.storage = SimpleNamespace(path=path)
        self.tickets = {"SA-test": {"owner_id": "alice", "state": "queued"}}
        self.plans = SuperAgentPlanBridge(self)
        self._cognitive_reads = Reader()

    def get(self, ticket_id, *, owner_id):
        ticket = self.tickets[ticket_id]
        if ticket["owner_id"] != owner_id:
            raise PermissionError("Owner mismatch")
        return dict(ticket)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Service(Path(tmp.name) / "brain.sqlite")
        self.b = self.s.plans

    def test_dependent_reads_and_receipts_survive_restart(self):
        self.b.create("SA-test", "alice", [
            {"id": "a", "title": "Version", "tool": "runtime.version"},
            {"id": "b", "title": "Platform", "tool": "runtime.platform"},
            {"id": "c", "title": "Trusted facts", "tool": "knowledge.search",
             "arguments": {"query": "verified knowledge"}, "depends_on": ["a", "b"]},
        ])
        result = execute_ready_plan_reads(self.s, "SA-test", "alice")
        self.assertEqual((result["state"], result["completed"], result["errors"]),
                         ("completed", 3, 0))
        self.assertEqual(self.s._cognitive_reads.calls[-1], "knowledge.search")
        snap = SuperAgentPlanBridge(self.s).snapshot("SA-test", "alice")
        self.assertTrue(all('"verified_receipt": true' in step["output"]
                            for step in snap["steps"]))
        self.assertTrue(all("token" not in step["output"] for step in snap["steps"]))

    def test_manual_step_is_not_claimed(self):
        self.b.create("SA-test", "alice", [
            {"id": "manual", "title": "Make human-only edit"},
            {"id": "read", "title": "Version", "tool": "runtime.version"},
            {"id": "after", "title": "Dependent", "tool": "runtime.platform",
             "depends_on": ["manual"]},
        ])
        result = execute_ready_plan_reads(self.s, "SA-test", "alice")
        self.assertEqual((result["state"], result["completed"]), ("active", 1))
        states = {x["id"]: x["state"] for x in self.b.snapshot("SA-test", "alice")["steps"]}
        self.assertEqual(states, {"manual": "queued", "read": "done", "after": "queued"})
        self.assertEqual(self.s._cognitive_reads.calls, ["runtime.version"])

    def test_owner_stop_and_pause_prevent_reads(self):
        self.b.create("SA-test", "alice", [
            {"id": "a", "title": "Version", "tool": "runtime.version"},
        ])
        with self.assertRaises(PermissionError):
            execute_ready_plan_reads(self.s, "SA-test", "other")
        self.b.control("SA-test", "alice", "pause")
        self.assertEqual(execute_ready_plan_reads(self.s, "SA-test", "alice")["state"], "paused")
        self.assertEqual(self.s._cognitive_reads.calls, [])
        self.b.control("SA-test", "alice", "resume")
        self.s.tickets["SA-test"]["state"] = "stopped"
        self.assertEqual(execute_ready_plan_reads(self.s, "SA-test", "alice")["completed"], 0)
        self.assertEqual(self.s._cognitive_reads.calls, [])

    def test_sql_tampering_cannot_execute_shell(self):
        self.b.create("SA-test", "alice", [
            {"id": "a", "title": "Version", "tool": "runtime.version"},
        ])
        with sqlite3.connect(self.s.storage.path) as db:
            db.execute("UPDATE durable_plan_steps SET tool_name='shell.exec'")
        result = execute_ready_plan_reads(self.s, "SA-test", "alice")
        self.assertEqual((result["completed"], result["errors"]), (0, 1))
        self.assertEqual(self.s._cognitive_reads.calls, [])

    def test_failure_requeues_without_runaway_retry(self):
        self.b.create("SA-test", "alice", [
            {"id": "a", "title": "Version", "tool": "runtime.version"},
        ])
        self.s._cognitive_reads.fail = True
        result = execute_ready_plan_reads(self.s, "SA-test", "alice")
        self.assertEqual((result["completed"], result["errors"]), (0, 1))
        self.assertEqual(len(self.s._cognitive_reads.calls), 1)
        self.assertEqual(self.b.snapshot("SA-test", "alice")["steps"][0]["state"], "queued")

    def test_chat_command_runs_explicit_read(self):
        intent = parse_superagent_intent("/agent plan run SA-test")
        self.assertEqual((intent.action, intent.ticket_id), ("plan_run", "SA-test"))
        self.b.create("SA-test", "alice", [
            {"id": "a", "title": "Version", "tool": "runtime.version"},
        ])
        reply = execute_plan_command(self.s, intent.action, intent.ticket_id, "alice")
        self.assertIn("подтверждённых чтений: 1", reply)
        self.assertIn("completed", reply)


if __name__ == "__main__":
    unittest.main()
