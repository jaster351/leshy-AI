"""Persistent SSH PTY security/lifecycle contracts; no real network traffic."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from leshy.superagent_ssh_pty import InteractiveSSHBroker


class FakeChannel:
    def __init__(self):
        self.closed = False
        self.command_bytes = []
        self.output = bytearray(b"shell ready\r\n")
        self.pty = None
        self.shell_open = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def get_pty(self, **kwargs):
        self.pty = kwargs

    def invoke_shell(self):
        self.shell_open = True

    def recv_ready(self):
        return bool(self.output)

    def recv(self, count):
        value = bytes(self.output[:count])
        del self.output[:count]
        return value

    def sendall(self, data):
        if self.closed:
            raise OSError("closed")
        self.command_bytes.append(data)

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, channel):
        self.channel = channel

    def open_session(self, timeout):
        return self.channel


class FakePolicy:
    def __init__(self):
        self.revoked = False
        self.audit_log = []
        self.storage = self

    def _check_actor(self, actor):
        if not actor.owner_id or not actor.session_fingerprint:
            raise PermissionError("invalid actor")

    def require(self, actor, node, capability, *, observed_fingerprint):
        self._check_actor(actor)
        if self.revoked or capability != "execute" or observed_fingerprint != "a" * 64:
            raise PermissionError("revoked")

    def audit(self, kind, payload):
        self.audit_log.append((kind, payload))


class SSHPTYTests(unittest.TestCase):
    def setUp(self):
        self.time = [0.0]
        self.policy = FakePolicy()
        self.actor = SimpleNamespace(owner_id="alice", session_fingerprint="cookie-hash")
        self.channel = FakeChannel()
        self.closed_connection = []
        self.broker = InteractiveSSHBroker(clock=lambda: self.time[0], ttl_seconds=30)
        self.addCleanup(self.broker.shutdown)

    def _open(self):
        channel = self.channel
        closed = self.closed_connection

        @contextmanager
        def connection(*_args, **_kwargs):
            try:
                yield FakeTransport(channel), "a" * 64
            finally:
                closed.append(True)

        with patch("leshy.superagent_ssh_pty.check_terminal_permission",
                   return_value="192.0.2.10"), patch(
            "leshy.superagent_ssh_pty._registered_pin", return_value="a" * 64,
        ), patch("leshy.superagent_ssh_pty.verified_connection",
                 side_effect=connection):
            result = self.broker.open(self.policy, self.actor, "192.0.2.10", "access.json")
        self.assertTrue(self.channel.shell_open)
        self.assertEqual(self.channel.pty["term"], "xterm")
        return result["session_id"]

    def test_persistent_shell_read_and_one_line_input(self):
        sid = self._open()
        self.assertIn("shell ready", self.broker.read(self.policy, self.actor, sid)["text"])
        self.assertEqual(self.broker.read(self.policy, self.actor, sid)["text"], "")
        with patch("leshy.superagent_ssh_pty.prepare_ssh_command",
                   return_value={"approval_token": "token"}) as prepare, patch(
            "leshy.superagent_ssh_pty.consume_ssh_approval"
        ) as consume:
            ticket = self.broker.prepare(self.policy, self.actor, sid, "pwd")
            self.assertEqual(ticket["approval_token"], "token")
            self.assertEqual(prepare.call_args.kwargs["approval_kind"], "pty-input")
            self.broker.write(self.policy, self.actor, sid, "pwd", "token")
            self.assertEqual(consume.call_args.kwargs["approval_kind"], "pty-input")
            self.assertEqual(prepare.call_args.args[4], consume.call_args.args[5])
        self.assertEqual(self.channel.command_bytes, [b"pwd\n"])
        self.assertNotIn("pwd", str(self.policy.audit_log))

    def test_reject_other_owner_and_browser_session_before_read_or_write(self):
        sid = self._open()
        others = [
            SimpleNamespace(owner_id="bob", session_fingerprint="cookie-hash"),
            SimpleNamespace(owner_id="alice", session_fingerprint="different"),
        ]
        for other in others:
            with self.subTest(other=other):
                with self.assertRaises(PermissionError):
                    self.broker.read(self.policy, other, sid)
                with self.assertRaises(PermissionError):
                    self.broker.prepare(self.policy, other, sid, "id")
                with self.assertRaises(PermissionError):
                    self.broker.close(self.policy, other, sid)
        self.assertEqual(self.channel.command_bytes, [])

    def test_grant_revoked_before_remote_input(self):
        sid = self._open()
        self.policy.revoked = True
        with self.assertRaises(PermissionError):
            self.broker.prepare(self.policy, self.actor, sid, "id")
        with self.assertRaises(PermissionError):
            self.broker.read(self.policy, self.actor, sid)
        self.assertEqual(self.channel.command_bytes, [])
        self.broker.close(self.policy, self.actor, sid)
        self.assertTrue(self.channel.closed)

    def test_passive_polls_do_not_extend_session_lifetime(self):
        sid = self._open()
        self.time[0] = 20.0
        self.broker.read(self.policy, self.actor, sid)
        self.time[0] = 31.0
        with self.assertRaises(PermissionError):
            self.broker.read(self.policy, self.actor, sid)
        self.assertTrue(self.channel.closed)
        self.assertTrue(self.closed_connection)

    def test_reject_control_character_and_multiline(self):
        sid = self._open()
        for line in ("echo hello\nwhoami", "a\r", "a\x00", "", "x" * 2049):
            with self.subTest(line=repr(line)):
                with self.assertRaises(ValueError):
                    self.broker.prepare(self.policy, self.actor, sid, line)
        self.assertEqual(self.channel.command_bytes, [])


    def test_new_connection_does_not_hold_the_broker_lock(self):
        seen = []

        @contextmanager
        def connection(*_args, **_kwargs):
            def inspect_from_another_thread():
                acquired = self.broker._lock.acquire(blocking=False)
                seen.append(acquired)
                if acquired:
                    self.broker._lock.release()

            thread = threading.Thread(target=inspect_from_another_thread)
            thread.start()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive(), "probe must finish without blocking")
            yield FakeTransport(self.channel), "a" * 64

        with patch("leshy.superagent_ssh_pty.check_terminal_permission",
                   return_value="192.0.2.10"), patch(
            "leshy.superagent_ssh_pty._registered_pin", return_value="a" * 64,
        ), patch("leshy.superagent_ssh_pty.verified_connection",
                 side_effect=connection):
            self.broker.open(self.policy, self.actor, "192.0.2.10", "access.json")
        self.assertEqual(seen, [True])

    def test_failed_open_audit_closes_channel_without_leaking_session(self):
        def unavailable_audit(*_args, **_kwargs):
            raise RuntimeError("audit storage unavailable")

        self.policy.audit = unavailable_audit
        with self.assertRaisesRegex(RuntimeError, "audit storage unavailable"):
            self._open()
        self.assertFalse(self.broker._sessions)
        self.assertTrue(self.channel.closed)
        self.assertTrue(self.closed_connection)

    def test_permission_denial_causes_no_network_connection(self):
        with patch("leshy.superagent_ssh_pty.check_terminal_permission",
                   side_effect=PermissionError("not enrolled")), patch(
            "leshy.superagent_ssh_pty.verified_connection"
        ) as ssh:
            with self.assertRaises(PermissionError):
                self.broker.open(self.policy, self.actor, "192.0.2.11", "access.json")
            ssh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
