"""Mock-only tests: approved ordered SSH plans fail stop and never retry."""
from __future__ import annotations

import json
import types
import unittest
from unittest import mock

from leshy.superagent_remote_permissions import RemotePermissionPolicy
from leshy.superagent_ssh_approvals import (
    prepare_ssh_plan, consume_ssh_plan_approval,
    consume_ssh_approval, prepare_ssh_checked_step,
)
from leshy.superagent_ssh_plan import (
    validate_ssh_plan, plan_approval_signature, execute_approved_ssh_plan, _RUN_LOCK,
)
from test_superagent_verified_ssh import HOST, PIN, Storage


def step(command: str, verify: str, expected: str = "active") -> dict[str, str]:
    return {"command": command, "verification_command": verify, "expected_text": expected}


def verified(status: str = "confirmed") -> dict:
    return {
        "node_id": HOST, "os_family": "Linux", "verdict": status,
        "action": {"exit_code": 0, "stdout": "PRIVATE ACTION", "stderr": "", "truncated": False},
        "verification": {
            "exit_code": 0 if status == "confirmed" else 1,
            "stdout": "active" if status == "confirmed" else "inactive",
            "stderr": "PRIVATE CHECK", "truncated": False
        } if status != "action_failed" else None,
    }


class SSHSequentialPlanTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = types.SimpleNamespace(
            storage=self.storage,
            owner_for_web_cookie=lambda raw: "alice" if raw == "valid-cookie" else "",
        )
        self.policy = RemotePermissionPolicy(self.identity, enrollment_verifier=lambda *_: True)
        self.actor = self.policy.actor_from_web_cookie("valid-cookie")
        self.policy.register_verified_node(self.actor, HOST, PIN, enrollment_proof="test")
        self.steps = [
            step("sudo systemctl restart nginx", "systemctl is-active nginx"),
            step("sudo systemctl restart php-fpm", "systemctl is-active php-fpm"),
            step("sudo systemctl restart redis", "systemctl is-active redis"),
        ]

    def tearDown(self):
        self.storage.db.close()

    def test_plan_schema_order_and_bounded_size(self):
        self.assertEqual(validate_ssh_plan(self.steps), self.steps)
        self.assertNotEqual(
            plan_approval_signature(self.steps),
            plan_approval_signature(list(reversed(self.steps))),
        )
        for invalid in (
            [], self.steps * 3, {"steps": self.steps}, [None],
            [{"command": "id", "verification_command": "true"}],
            [step("x", "true") | {"extra": "execute-anything"}],
            [step("x", "")], [step("x", "true", "A" * 513)],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_ssh_plan(invalid)

    def test_approval_bound_to_order_all_checks_owner_and_session(self):
        receipt = prepare_ssh_plan(self.policy, self.actor, HOST, "access.json", self.steps)
        self.assertEqual(receipt["steps"], 3)
        self.assertEqual(len(receipt["plan_sha256"]), 64)
        with self.assertRaises(PermissionError):
            consume_ssh_plan_approval(
                self.policy, self.actor, receipt["approval_token"], HOST, "access.json",
                list(reversed(self.steps)),
            )
        with self.assertRaises(PermissionError):
            consume_ssh_plan_approval(
                self.policy, self.actor, receipt["approval_token"], HOST, "access.json",
                self.steps,
            )
        receipt = prepare_ssh_plan(self.policy, self.actor, HOST, "access.json", self.steps)
        changed = [dict(row) for row in self.steps]
        changed[1]["expected_text"] = "inactive"
        with self.assertRaises(PermissionError):
            consume_ssh_plan_approval(
                self.policy, self.actor, receipt["approval_token"], HOST, "access.json",
                changed,
            )
        receipt = prepare_ssh_plan(self.policy, self.actor, HOST, "access.json", self.steps)
        with self.assertRaises(PermissionError):
            consume_ssh_approval(
                self.policy, self.actor, receipt["approval_token"], HOST, "access.json",
                plan_approval_signature(self.steps), approval_kind="terminal-command",
            )
        receipt = prepare_ssh_plan(self.policy, self.actor, HOST, "access.json", self.steps)
        consume_ssh_plan_approval(
            self.policy, self.actor, receipt["approval_token"], HOST, "access.json", self.steps,
        )
        with self.assertRaises(PermissionError):
            consume_ssh_plan_approval(
                self.policy, self.actor, receipt["approval_token"], HOST, "access.json",
                self.steps,
            )

    def test_successful_plan_orders_and_verifies_every_step(self):
        with mock.patch(
            "leshy.superagent_ssh_plan.run_ssh_checked_step",
            side_effect=[verified(), verified(), verified()],
        ) as run:
            result = execute_approved_ssh_plan(
                self.policy, self.actor, HOST, "access.json", self.steps,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["confirmed_steps"], 3)
        self.assertEqual(result["remaining_not_started"], 0)
        self.assertEqual(run.call_count, 3)
        self.assertEqual([call.args[4] for call in run.call_args_list],
                         [x["command"] for x in self.steps])
        self.assertEqual([call.args[5] for call in run.call_args_list],
                         [x["verification_command"] for x in self.steps])
        log = json.dumps(self.storage.audit_events)
        self.assertNotIn("PRIVATE ACTION", log)
        self.assertNotIn("PRIVATE CHECK", log)
        self.assertNotIn("sudo systemctl", log)
        self.assertIn("superagent.ssh.plan", log)

    def test_failed_verification_stops_without_running_next_steps(self):
        for state, expected_status in (
            ("not_confirmed", "stopped"),
            ("action_failed", "stopped"),
            ("verification_unavailable", "uncertain"),
        ):
            with self.subTest(state=state), mock.patch(
                "leshy.superagent_ssh_plan.run_ssh_checked_step",
                side_effect=[verified(), verified(state), verified()],
            ) as run:
                outcome = execute_approved_ssh_plan(
                    self.policy, self.actor, HOST, "access.json", self.steps,
                )
            self.assertEqual(outcome["status"], expected_status)
            self.assertEqual(outcome["confirmed_steps"], 1)
            self.assertEqual(outcome["stopped_at"], 1)
            self.assertEqual(outcome["remaining_not_started"], 1)
            self.assertEqual(run.call_count, 2)
            self.assertFalse(outcome["auto_retry"])

    def test_network_error_is_uncertain_no_retry(self):
        with mock.patch(
            "leshy.superagent_ssh_plan.run_ssh_checked_step",
            side_effect=[verified(), TimeoutError("private network diagnostics")],
        ) as run:
            outcome = execute_approved_ssh_plan(
                self.policy, self.actor, HOST, "access.json", self.steps,
            )
        self.assertEqual(outcome["status"], "uncertain")
        self.assertEqual(outcome["steps"][1]["verdict"], "unknown")
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("private network diagnostics", json.dumps(outcome))

    def test_permission_or_module_revocation_before_next_step(self):
        checks = iter([True, False])
        with mock.patch(
            "leshy.superagent_ssh_plan.run_ssh_checked_step",
            return_value=verified(),
        ) as run:
            outcome = execute_approved_ssh_plan(
                self.policy, self.actor, HOST, "access.json", self.steps,
                may_continue=lambda: next(checks),
            )
        self.assertEqual(outcome["status"], "stopped")
        self.assertEqual(outcome["confirmed_steps"], 1)
        self.assertEqual(run.call_count, 1)

    def test_unverified_actor_never_reaches_ssh(self):
        outsider = type(self.actor)("other", self.actor.session_fingerprint, "web")
        with mock.patch("leshy.superagent_ssh_plan.run_ssh_checked_step") as run:
            with self.assertRaises(PermissionError):
                execute_approved_ssh_plan(
                    self.policy, outsider, HOST, "access.json", self.steps,
                )
            run.assert_not_called()

    def test_second_plan_cannot_interleave(self):
        _RUN_LOCK.acquire()
        try:
            with mock.patch("leshy.superagent_ssh_plan.run_ssh_checked_step") as run:
                with self.assertRaises(PermissionError):
                    execute_approved_ssh_plan(
                        self.policy, self.actor, HOST, "access.json", self.steps,
                    )
                run.assert_not_called()
        finally:
            _RUN_LOCK.release()


if __name__ == "__main__":
    unittest.main()
