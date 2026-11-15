"""Offline regression checks for native OpenSSH transport."""
from __future__ import annotations

import unittest
from unittest import mock

from leshy.superagent_native_ssh import run_native_ssh_command


class NativeSSHValidationTests(unittest.TestCase):
    def test_rejects_invalid_inputs_before_policy_or_network(self):
        policy = mock.Mock()
        actor = object()
        cases = [
            ("hostname.local", "operator", "id", 20),
            ("127.0.0.1", "-oProxyCommand=evil", "id", 20),
            ("127.0.0.1", "operator", "", 20),
            ("127.0.0.1", "operator", "id\x00evil", 20),
            ("127.0.0.1", "operator", "id", 31),
            ("127.0.0.1", "operator", "x" * 4097, 20),
        ]
        for host, user, command, timeout in cases:
            with self.subTest(host=host, user=user, command=command[:20]):
                with self.assertRaises(ValueError):
                    run_native_ssh_command(
                        policy, actor, host, user, command, timeout=timeout
                    )
        policy.assert_not_called()

    def test_rejects_mismatched_host_key_before_ssh(self):
        policy = mock.Mock()
        actor = object()
        with mock.patch(
            "leshy.superagent_native_ssh._registered_pin",
            return_value="0" * 64,
        ), mock.patch(
            "leshy.superagent_native_ssh.shutil.which",
            side_effect=lambda name: name,
        ), mock.patch(
            "leshy.superagent_native_ssh.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=b"127.0.0.1 ssh-ed25519 AAAA\n"),
        ) as run:
            with self.assertRaises(PermissionError):
                run_native_ssh_command(policy, actor, "127.0.0.1", "operator", "id")
        self.assertEqual(run.call_count, 1)
        policy.require.assert_not_called()


if __name__ == "__main__":
    unittest.main()
