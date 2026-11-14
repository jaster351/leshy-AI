"""Mock-only regression checks for approved two-command SSH postconditions."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import types
import unittest
from unittest import mock

from leshy.superagent_remote_permissions import RemotePermissionPolicy
from leshy.superagent_ssh_approvals import (
    prepare_ssh_checked_step, consume_ssh_checked_approval,
    consume_ssh_approval,
)
from leshy.superagent_ssh_checked_step import (
    run_ssh_checked_step, validate_checked_step,
)
from test_superagent_verified_ssh import HOST, PIN, Storage


def result(code=0, *, stdout="", stderr="", truncated=False):
    return {"exit_code": code, "stdout": stdout, "stderr": stderr,
            "truncated": truncated}


class SSHCheckedStepTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = types.SimpleNamespace(
            storage=self.storage,
            owner_for_web_cookie=lambda raw: "alice" if raw == "valid-cookie" else "",
        )
        self.policy = RemotePermissionPolicy(
            self.identity, enrollment_verifier=lambda *_: True,
        )
        self.actor = self.policy.actor_from_web_cookie("valid-cookie")
        self.policy.register_verified_node(
            self.actor, HOST, PIN, enrollment_proof="test-attestation",
        )

    def tearDown(self):
        self.storage.db.close()

    def _run(self, results, *, expected="active", **kwargs):
        @contextmanager
        def connected(*args, **kw):
            yield object(), PIN

        with mock.patch("leshy.superagent_ssh_checked_step.verified_connection",
                        side_effect=connected) as connect, mock.patch(
            "leshy.superagent_ssh_checked_step._detect_remote_os",
            return_value="Linux"
        ) as detect, mock.patch(
            "leshy.superagent_ssh_checked_step._bounded_exec",
            side_effect=results,
        ) as executor:
            response = run_ssh_checked_step(
                self.policy, self.actor, HOST, "server.json",
                "sudo systemctl restart nginx",
                "systemctl is-active nginx",
                expected,
            )
        self.assertEqual(detect.call_count, 1)
        self.assertEqual(connect.call_count, 1)
        return response, executor.call_args_list

    def test_confirmed_requires_separate_successful_check(self):
        answer, calls = self._run(
            [result(stdout="restarted"), result(stdout="active\n")],
        )
        self.assertEqual(answer["verdict"], "confirmed")
        self.assertTrue(answer["expected_text_matched"])
        self.assertEqual(len(calls), 2)
        self.assertEqual([call.args[1] for call in calls], [
            "sudo systemctl restart nginx", "systemctl is-active nginx",
        ])
        audit = json.dumps(self.storage.audit_events)
        self.assertIn("superagent.ssh.checked_step", audit)
        self.assertNotIn("sudo systemctl restart nginx", audit)
        self.assertNotIn("restarted", audit)

    def test_action_failure_never_executes_verification(self):
        for execution in (result(1, stderr="failed"), result(0, truncated=True)):
            with self.subTest(execution=execution):
                answer, calls = self._run([execution])
                self.assertEqual(answer["verdict"], "action_failed")
                self.assertIsNone(answer["verification"])
                self.assertEqual(len(calls), 1)

    def test_failed_check_or_missing_text_is_not_success(self):
        for check in (result(1, stdout="active"), result(stdout="inactive"),
                      result(stdout="active", truncated=True)):
            with self.subTest(check=check):
                answer, calls = self._run([result(), check])
                self.assertEqual(answer["verdict"], "not_confirmed")
                self.assertFalse(answer["expected_text_matched"])
                self.assertEqual(len(calls), 2)

    def test_revocation_or_error_during_check_is_unknown(self):
        answer, calls = self._run([result(), TimeoutError("connection dropped")])
        self.assertEqual(answer["verdict"], "verification_unavailable")
        self.assertIsNone(answer["verification"])
        self.assertEqual(len(calls), 2)

    def test_approval_is_single_use_and_bound_to_both_commands(self):
        command = "systemctl restart nginx"
        verification = "systemctl is-active nginx"
        token = prepare_ssh_checked_step(
            self.policy, self.actor, HOST, "server.json",
            command, verification, "active",
        )
        self.assertEqual(token["commands"], 2)
        self.assertEqual(len(token["step_sha256"]), 64)
        # Neither changing the command nor tampering with the postcondition
        # can reuse the token for another action; the token is consumed on deny.
        with self.assertRaises(PermissionError):
            consume_ssh_checked_approval(
                self.policy, self.actor, token["approval_token"], HOST, "server.json",
                command, verification, "inactive",
            )
        with self.assertRaises(PermissionError):
            consume_ssh_checked_approval(
                self.policy, self.actor, token["approval_token"], HOST, "server.json",
                command, verification, "active",
            )
        token = prepare_ssh_checked_step(
            self.policy, self.actor, HOST, "server.json",
            command, verification, "active",
        )
        with self.assertRaises(PermissionError):
            consume_ssh_approval(
                self.policy, self.actor, token["approval_token"], HOST, "server.json",
                "ssh-checked-step-v1:" + token["step_sha256"],
            )
        token = prepare_ssh_checked_step(
            self.policy, self.actor, HOST, "server.json",
            command, verification, "active",
        )
        consume_ssh_checked_approval(
            self.policy, self.actor, token["approval_token"], HOST, "server.json",
            command, verification, "active",
        )
        with self.assertRaises(PermissionError):
            consume_ssh_checked_approval(
                self.policy, self.actor, token["approval_token"], HOST, "server.json",
                command, verification, "active",
            )

    def test_invalid_spec_and_unauthorized_node_fail_before_ssh(self):
        invalid = (("", "true", ""), ("echo hello", "", ""),
                   ("hello\0", "true", ""), ("true", "true", "x"*513))
        for command, verifier, expected in invalid:
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    validate_checked_step(command, verifier, expected)
        outsider = type(self.actor)("outsider", self.actor.session_fingerprint, "web")
        with mock.patch("leshy.superagent_ssh_checked_step.verified_connection") as connect:
            with self.assertRaises(PermissionError):
                run_ssh_checked_step(
                    self.policy, outsider, HOST, "server.json",
                    "echo hello", "true",
                )
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
