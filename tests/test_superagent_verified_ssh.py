"""Regression tests for real pre-auth SSH verification; never connect to a host."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import types
import unittest
from unittest import mock

from leshy.superagent_remote_permissions import RemotePermissionPolicy
from leshy.superagent_verified_ssh import inspect_remote_os, _node_ip


HOST = "192.168.5.12"
KEY = b"trusted-ssh-host-public-key"
PIN = hashlib.sha256(KEY).hexdigest()


class Storage:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.audit_events = []
        self.db.executescript("""
        CREATE TABLE owner_web_sessions(session_hash TEXT,owner_id TEXT,expires_at REAL);
        CREATE TABLE owner_identity_links(owner_id TEXT,provider TEXT,provider_user_id TEXT);
        """)
        self.db.execute(
            "INSERT INTO owner_web_sessions VALUES(?,?,?)",
            (hashlib.sha256(b"valid-cookie").hexdigest(), "alice", 9999999999),
        )
        self.db.execute(
            "INSERT INTO owner_identity_links VALUES(?,?,?)", ("alice", "github", "alice-gh")
        )

    def connection(self):
        return self.db

    def audit(self, action, detail):
        self.audit_events.append((action, detail))


class FakeChannel:
    def __init__(self):
        self.command = ""
        self._done = False

    def settimeout(self, value):
        pass

    def exec_command(self, command):
        self.command = command

    def recv_ready(self):
        return not self._done

    def recv(self, size):
        self._done = True
        return b"Linux\n" if self.command == "uname -s" else b""

    def exit_status_ready(self):
        return self._done

    def recv_exit_status(self):
        return 0

    def close(self):
        pass


class FakeTransport:
    def __init__(self, sock):
        self.sock = sock
        self.auth_calls = []
        self.commands = []
        self.closed = False
        self.host_key = KEY

    def start_client(self, timeout):
        pass

    def get_remote_server_key(self):
        return types.SimpleNamespace(asbytes=lambda: self.host_key)

    def auth_password(self, username, password, fallback=False):
        self.auth_calls.append((username, password, fallback))

    def is_authenticated(self):
        return bool(self.auth_calls)

    def open_session(self, timeout):
        channel = FakeChannel()
        self.commands.append(channel)
        return channel

    def close(self):
        self.closed = True


class SuperAgentVerifiedSSHTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.identity = types.SimpleNamespace(
            storage=self.storage,
            owner_for_web_cookie=lambda c: "alice" if c == "valid-cookie" else "",
        )
        self.policy = RemotePermissionPolicy(
            self.identity, enrollment_verifier=lambda *_args: True
        )
        self.actor = self.policy.actor_from_web_cookie("valid-cookie")

    def tearDown(self):
        self.storage.db.close()

    def enroll(self, key=PIN):
        self.policy.register_verified_node(
            self.actor, HOST, key, enrollment_proof="server-side-proof"
        )

    def test_unregistered_ip_rejected_before_secret_read_or_network(self):
        with mock.patch("leshy.superagent_verified_ssh._credential") as credential, mock.patch(
            "leshy.superagent_verified_ssh.socket.create_connection"
        ) as connect:
            with self.assertRaises(PermissionError):
                inspect_remote_os(self.policy, self.actor, HOST, "credentials.json")
            credential.assert_not_called()
            connect.assert_not_called()

    def test_bad_ip_rejected_without_network(self):
        self.assertRaises(ValueError, _node_ip, "example.com")
        self.assertRaises(ValueError, _node_ip, "224.0.0.1")

    def test_cancelled_ticket_never_reads_credentials(self):
        self.enroll()
        stop = threading.Event()
        stop.set()
        with mock.patch("leshy.superagent_verified_ssh._credential") as credential:
            with self.assertRaises(InterruptedError):
                inspect_remote_os(
                    self.policy, self.actor, HOST, "credentials.json", cancel_event=stop
                )
            credential.assert_not_called()

    def test_trusted_key_allows_only_fixed_readonly_os_command(self):
        self.enroll()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.json"
            path.write_text(
                json.dumps({"host": HOST, "username": "operator", "password": "secret"}),
                encoding="utf-8",
            )
            transports = []
            sock = mock.Mock()
            def construct(sock_arg):
                obj = FakeTransport(sock_arg)
                transports.append(obj)
                return obj
            with mock.patch(
                "leshy.superagent_verified_ssh.find_credential_file", return_value=path
            ), mock.patch(
                "leshy.superagent_verified_ssh.socket.create_connection", return_value=sock
            ), mock.patch.dict(
                "sys.modules", {"paramiko": types.SimpleNamespace(Transport=construct)}
            ):
                result = inspect_remote_os(self.policy, self.actor, HOST, "credentials.json")
        self.assertEqual(result["os_family"], "Linux")
        self.assertTrue(result["verified"])
        self.assertEqual([c.command for c in transports[0].commands], ["uname -s"])
        self.assertEqual(transports[0].auth_calls, [("operator", "secret", False)])
        self.assertTrue(transports[0].closed)
        self.assertTrue(sock.close.called)
        self.assertEqual(len(self.storage.audit_events), 1)
        self.assertNotIn("secret", json.dumps(self.storage.audit_events))

    def test_mismatched_server_key_never_receives_password(self):
        self.enroll()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.json"
            path.write_text(
                json.dumps({"host": HOST, "username": "operator", "password": "secret"}),
                encoding="utf-8",
            )
            transports = []
            def construct(sock):
                obj = FakeTransport(sock)
                obj.host_key = b"attacker-public-key"
                transports.append(obj)
                return obj
            with mock.patch(
                "leshy.superagent_verified_ssh.find_credential_file", return_value=path
            ), mock.patch(
                "leshy.superagent_verified_ssh.socket.create_connection",
                return_value=mock.Mock(),
            ), mock.patch.dict(
                "sys.modules", {"paramiko": types.SimpleNamespace(Transport=construct)}
            ):
                with self.assertRaises(PermissionError):
                    inspect_remote_os(self.policy, self.actor, HOST, "credentials.json")
            self.assertEqual(transports[0].auth_calls, [])
            self.assertEqual(transports[0].commands, [])

    def test_wrong_owner_credentials_cannot_inspect(self):
        self.enroll()
        foreign = self.policy.actor_from_web_cookie("valid-cookie")
        foreign = type(foreign)("other", foreign.session_fingerprint, foreign.method)
        with mock.patch("leshy.superagent_verified_ssh.socket.create_connection") as connect:
            with self.assertRaises(PermissionError):
                inspect_remote_os(self.policy, foreign, HOST, "credentials.json")
            connect.assert_not_called()


    def test_web_route_enforces_cookie_ticket_module_and_origin(self):
        route = (Path(__file__).resolve().parents[1] / "src" / "leshy" /
                 "web_server_impl14.py").read_text(encoding="utf-8")
        segment = route.split('if path == "/v1/superagent/ssh/inspect":', 1)[1].split(
            'if path != "/v1/superagent/task":', 1
        )[0]
        for required in (
            'self._dashboard_context()', 'module_enabled(app.storage, "remote-admin")',
            '"X-Leshy-UI"', '"Origin"', '"Content-Type"',
            'RemotePermissionPolicy(identity)', 'policy.actor_from_web_cookie',
            'service.get(ticket_id, owner_id=owner)',
            'ticket.get("state") != "working"', 'inspect_remote_os(',
        ):
            self.assertIn(required, segment)
        self.assertNotIn("subprocess", segment)
        self.assertNotIn("exec_command(", segment)


if __name__ == "__main__":
    unittest.main()
