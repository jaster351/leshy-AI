"""Regression coverage for broker-bound browser approvals (not run locally)."""
import unittest

from leshy.action_approvals import ApprovalBroker
from leshy.browser_agent import (
    ApprovalRequired, BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk,
)


class RecordingAdapter:
    adapter_id = "recording"

    def __init__(self):
        self.actions = []

    def available(self):
        return True

    def execute(self, action):
        self.actions.append(action)
        return BrowserActionResult("ok", "executed")


class BrowserApprovalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RecordingAdapter()
        self.agent = BrowserAgent([self.adapter])
        self.broker = ApprovalBroker(ttl_seconds=60)
        self.action = BrowserAction("agent_commit", "https://example.org",
                                    BrowserRisk.COMMIT, {"task": "submit"})

    def test_legacy_approved_flag_cannot_bypass_gate(self):
        with self.assertRaises(ApprovalRequired):
            self.agent.execute(self.action, approved=True)
        self.assertEqual(self.adapter.actions, [])

    def test_approved_action_runs_once_and_sets_internal_marker(self):
        item = self.broker.request("owner-a", self.action, session_id="session-a")
        result = self.agent.execute_approved(
            self.action, broker=self.broker, approval_id=item.approval_id,
            owner_id="owner-a", session_id="session-a")
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(self.adapter.actions), 1)
        self.assertTrue(self.adapter.actions[0].arguments["_approval_verified"])
        with self.assertRaises(KeyError):
            self.agent.execute_approved(
                self.action, broker=self.broker, approval_id=item.approval_id,
                owner_id="owner-a", session_id="session-a")
        self.assertEqual(len(self.adapter.actions), 1)

    def test_cross_owner_session_and_changed_action_denied(self):
        item = self.broker.request("owner-a", self.action, session_id="session-a")
        changed = BrowserAction("agent_commit", "https://example.org",
                                BrowserRisk.COMMIT, {"task": "different"})
        for owner, session, action in [
            ("owner-b", "session-a", self.action),
            ("owner-a", "session-b", self.action),
            ("owner-a", "session-a", changed),
        ]:
            with self.assertRaises(KeyError):
                self.agent.execute_approved(
                    action, broker=self.broker, approval_id=item.approval_id,
                    owner_id=owner, session_id=session)
        self.assertEqual(self.adapter.actions, [])
        self.agent.execute_approved(self.action, broker=self.broker,
                                    approval_id=item.approval_id,
                                    owner_id="owner-a", session_id="session-a")

    def test_denied_action_never_executes(self):
        item = self.broker.request("owner-a", self.action, session_id="session-a")
        self.broker.deny("owner-a", item.approval_id, session_id="session-a")
        with self.assertRaises(KeyError):
            self.agent.execute_approved(self.action, broker=self.broker,
                                        approval_id=item.approval_id,
                                        owner_id="owner-a", session_id="session-a")
        self.assertEqual(self.adapter.actions, [])

    def test_model_supplied_marker_is_stripped(self):
        forged = BrowserAction("agent_commit", "https://example.org",
                               BrowserRisk.COMMIT, {"_approval_verified": True})
        with self.assertRaises(ApprovalRequired):
            self.agent.execute(forged)
        self.assertEqual(self.adapter.actions, [])


if __name__ == "__main__":
    unittest.main()
