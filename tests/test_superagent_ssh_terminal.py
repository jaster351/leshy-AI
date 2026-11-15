"""Regression contracts for full SSH command execution and safe enrollment.

All SSH and network operations are mocks. Tests do not touch real servers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import types
import unittest
from unittest import mock

from leshy.superagent_remote_permissions import RemotePermissionPolicy
from leshy.superagent_ssh_terminal import run_ssh_terminal_command
from leshy.superagent_ssh_enrollment import enroll_verified_ssh_node, parse_ssh_fingerprint
from leshy.superagent_ssh_approvals import prepare_ssh_command, consume_ssh_approval
from test_superagent_verified_ssh import HOST, KEY, PIN, Storage, FakeTransport, FakeChannel


class TerminalChannel(FakeChannel):
    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, size):
        return b""


class TerminalTransport(FakeTransport):
    def open_session(self, timeout):
        channel = TerminalChannel()
        self.commands.append(channel)
        return channel


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = types.SimpleNamespace(
            storage=self.storage,
            owner_for_web_cookie=lambda c: "alice" if c == "valid-cookie" else "",
        )
        self.policy = RemotePermissionPolicy(
            self.identity, enrollment_verifier=lambda *_: True,
        )
        self.actor = self.policy.actor_from_web_cookie("valid-cookie")
        self.transports = []

    def tearDown(self):
        self.storage.db.close()

    def mock_ssh(self, key=KEY):
        tests = self
        def construct(sock):
            client = TerminalTransport(sock)
            client.host_key = key
            tests.transports.append(client)
            return client
        stack = mock.patch.multiple(
            "leshy.superagent_ssh_terminal",
            _credential=mock.DEFAULT,
        )
        # The enrollment module also has its own imported credential resolver.
        return construct

    def test_fingerprint_formats(self):
        standard = "SHA256:" + base64.b64encode(bytes.fromhex(PIN)).decode().rstrip("=")
        self.assertEqual(parse_ssh_fingerprint(PIN.upper()), PIN)
        self.assertEqual(parse_ssh_fingerprint(standard), PIN)
        for invalid in ("", "SHA256:notvalid", "0" * 62, "MD5:00:aa"):
            with self.assertRaises(ValueError):
                parse_ssh_fingerprint(invalid)

    def test_enrollment_requires_matching_live_host_key_and_login(self):
        def make(sock):
            obj = TerminalTransport(sock)
            self.transports.append(obj)
            return obj
        with mock.patch("leshy.superagent_ssh_enrollment._credential",
                        return_value=("operator", "SECRET")), mock.patch(
            "leshy.superagent_ssh_enrollment.socket.create_connection",
            return_value=mock.Mock()
        ), mock.patch.dict("sys.modules", {
            "paramiko": types.SimpleNamespace(Transport=make)
        }):
            result = enroll_verified_ssh_node(
                self.policy, self.actor, HOST, "access.json", PIN
            )
        self.assertTrue(result["enrolled"])
        self.assertEqual(self.transports[0].auth_calls, [("operator", "SECRET", False)])
        with self.storage.connection() as db:
            saved = db.execute(
                "SELECT host_fingerprint FROM superagent_remote_nodes WHERE node_id=?",
                (HOST,),
            ).fetchone()
        self.assertEqual(saved["host_fingerprint"], PIN)
        self.assertNotIn("SECRET", json.dumps(self.storage.audit_events))

    def test_mismatched_key_never_sends_password_and_does_not_enroll(self):
        transports = []
        def make(sock):
            obj = TerminalTransport(sock)
            obj.host_key = b"impostor-host-key"
            transports.append(obj)
            return obj
        with mock.patch("leshy.superagent_ssh_enrollment._credential",
                        return_value=("operator", "SECRET")), mock.patch(
            "leshy.superagent_ssh_enrollment.socket.create_connection",
            return_value=mock.Mock()
        ), mock.patch.dict("sys.modules", {
            "paramiko": types.SimpleNamespace(Transport=make)
        }):
            with self.assertRaises(PermissionError):
                enroll_verified_ssh_node(
                    self.policy, self.actor, HOST, "access.json", PIN
                )
        self.assertEqual(transports[0].auth_calls, [])
        with self.storage.connection() as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM superagent_remote_nodes WHERE node_id=?", (HOST,)
            ).fetchone())

    def test_full_command_after_approval_checks_os_and_does_not_log_output(self):
        self.policy.register_verified_node(
            self.actor, HOST, PIN, enrollment_proof="test"
        )
        request = prepare_ssh_command(
            self.policy, self.actor, HOST, "access.json", "sudo systemctl restart example"
        )
        with self.assertRaises(PermissionError):
            consume_ssh_approval(
                self.policy, self.actor, request["approval_token"], HOST,
                "access.json", "sudo reboot"
            )
        request = prepare_ssh_command(
            self.policy, self.actor, HOST, "access.json", "sudo systemctl restart example"
        )
        consume_ssh_approval(
            self.policy, self.actor, request["approval_token"], HOST,
            "access.json", "sudo systemctl restart example"
        )
        with self.assertRaises(PermissionError):
            consume_ssh_approval(
                self.policy, self.actor, request["approval_token"], HOST,
                "access.json", "sudo systemctl restart example"
            )
        def make(sock):
            obj = TerminalTransport(sock)
            self.transports.append(obj)
            return obj
        with mock.patch("leshy.superagent_ssh_terminal._credential",
                        return_value=("operator", "SECRET")), mock.patch(
            "leshy.superagent_ssh_terminal.socket.create_connection",
            return_value=mock.Mock()
        ), mock.patch.dict("sys.modules", {
            "paramiko": types.SimpleNamespace(Transport=make)
        }):
            answer = run_ssh_terminal_command(
                self.policy, self.actor, HOST, "access.json",
                "sudo systemctl restart example",
            )
        self.assertEqual(answer["os_family"], "Linux")
        self.assertEqual(answer["exit_code"], 0)
        self.assertEqual([c.command for c in self.transports[0].commands], [
            "uname -s", "sudo systemctl restart example"
        ])
        self.assertNotIn("SECRET", json.dumps(self.storage.audit_events))
        self.assertNotIn("sudo systemctl restart example", json.dumps(self.storage.audit_events))

    def test_execute_permission_is_checked_before_network_and_credentials(self):
        other_node = "192.168.1.55"
        with mock.patch("leshy.superagent_ssh_terminal._credential") as credential, mock.patch(
            "leshy.superagent_ssh_terminal.socket.create_connection"
        ) as connection:
            with self.assertRaises(PermissionError):
                run_ssh_terminal_command(
                    self.policy, self.actor, other_node, "access.json", "id"
                )
            credential.assert_not_called()
            connection.assert_not_called()

    def test_cannot_enroll_without_provider_identity(self):
        invalid_actor = type(self.actor)("impostor", self.actor.session_fingerprint, "web")
        with mock.patch("leshy.superagent_ssh_enrollment.socket.create_connection") as connect:
            with self.assertRaises(PermissionError):
                enroll_verified_ssh_node(
                    self.policy, invalid_actor, HOST, "access.json", PIN
                )
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
