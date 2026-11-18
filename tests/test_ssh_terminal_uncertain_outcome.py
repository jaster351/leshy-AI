"""Regression checks for SSH commands with uncertain remote side effects.

These tests intentionally use fakes; they do not assert real SSH E2E readiness.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from leshy import superagent_ssh_terminal as ssh


class FakeStorage:
    def __init__(self):
        self.events = []

    def audit(self, name, payload):
        self.events.append((name, payload))


class FakePolicy:
    def __init__(self):
        self.storage = FakeStorage()
        self.calls = []

    def require(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@contextmanager
def fake_connection(*args, **kwargs):
    yield object(), "a" * 64


@pytest.mark.parametrize("failure", [
    TimeoutError("timeout"), ConnectionError("disconnected"), OSError("socket closed"),
])
def test_ssh_uncertain_failure_is_not_reported_as_safe_retry(failure):
    policy = FakePolicy()
    actor = SimpleNamespace(owner_id="owner")
    with patch.object(ssh, "_node_ip", return_value="192.0.2.10"), \
         patch.object(ssh, "_registered_pin", return_value="a" * 64), \
         patch.object(ssh, "verified_connection", fake_connection), \
         patch.object(ssh, "_detect_remote_os", return_value="Linux"), \
         patch.object(ssh, "_bounded_exec", side_effect=failure) as execute:
        with pytest.raises(ssh.SSHOutcomeUnknown, match="unknown"):
            ssh.run_ssh_terminal_command(policy, actor, "192.0.2.10", "credential", "touch /tmp/x")
    execute.assert_called_once()
    assert policy.storage.events[-1][1]["outcome"] == "unknown"
    assert "touch /tmp/x" not in str(policy.storage.events)


@pytest.mark.parametrize(("result", "expected"), [
    ({"exit_code": 0, "truncated": False, "stdout": "ok", "stderr": ""}, "confirmed"),
    ({"exit_code": 1, "truncated": False, "stdout": "", "stderr": "failed"}, "confirmed"),
    ({"exit_code": None, "truncated": True, "stdout": "partial", "stderr": ""}, "unknown"),
])
def test_ssh_result_outcome_distinguishes_complete_from_partial(result, expected):
    policy = FakePolicy()
    actor = SimpleNamespace(owner_id="owner")
    with patch.object(ssh, "_node_ip", return_value="192.0.2.10"), \
         patch.object(ssh, "_registered_pin", return_value="a" * 64), \
         patch.object(ssh, "verified_connection", fake_connection), \
         patch.object(ssh, "_detect_remote_os", return_value="Linux"), \
         patch.object(ssh, "_bounded_exec", return_value=result):
        output = ssh.run_ssh_terminal_command(policy, actor, "192.0.2.10", "credential", "echo hi")
    assert output["outcome"] == expected
    assert policy.storage.events[-1][1]["outcome"] == expected
