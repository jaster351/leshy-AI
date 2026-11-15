"""Mocked owner approval boundary for native OpenSSH.

No network or real SSH server is used.
"""
from __future__ import annotations

import unittest
from unittest import mock

from leshy.superagent_native_ssh_approval import (
    prepare_native_ssh_command,
    execute_approved_native_ssh_command,
)


class NativeApprovalBridgeTests(unittest.TestCase):
    def test_preparation_binds_username(self):
        with mock.patch(
            "leshy.superagent_native_ssh_approval.prepare_ssh_command",
            return_value={"approval_token": "receipt"},
        ) as prepare:
            result = prepare_native_ssh_command(
                mock.sentinel.policy, mock.sentinel.actor,
                "127.0.0.1", "operator", "id",
            )
        self.assertEqual(result["transport"], "native-openssh")
        self.assertEqual(result["username"], "operator")
        prepare.assert_called_once_with(
            mock.sentinel.policy, mock.sentinel.actor,
            "127.0.0.1", "operator", "id", approval_kind="native-openssh",
        )

    def test_approval_consumed_before_transport(self):
        with mock.patch(
            "leshy.superagent_native_ssh_approval.consume_ssh_approval",
            side_effect=PermissionError("already used"),
        ), mock.patch(
            "leshy.superagent_native_ssh_approval.run_native_ssh_command",
        ) as execute:
            with self.assertRaises(PermissionError):
                execute_approved_native_ssh_command(
                    mock.sentinel.policy, mock.sentinel.actor,
                    "receipt", "127.0.0.1", "operator", "id",
                )
        execute.assert_not_called()

    def test_valid_receipt_executes_once(self):
        with mock.patch(
            "leshy.superagent_native_ssh_approval.consume_ssh_approval",
        ) as consume, mock.patch(
            "leshy.superagent_native_ssh_approval.run_native_ssh_command",
            return_value={"exit_code": 0},
        ) as execute:
            result = execute_approved_native_ssh_command(
                mock.sentinel.policy, mock.sentinel.actor,
                "receipt", "127.0.0.1", "operator", "id",
            )
        self.assertEqual(result["exit_code"], 0)
        consume.assert_called_once()
        execute.assert_called_once()

    def test_invalid_username_never_creates_approval(self):
        with mock.patch(
            "leshy.superagent_native_ssh_approval.prepare_ssh_command",
        ) as prepare:
            with self.assertRaises(ValueError):
                prepare_native_ssh_command(
                    mock.sentinel.policy, mock.sentinel.actor,
                    "127.0.0.1", "-oProxyCommand=bad", "id",
                )
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
