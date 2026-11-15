"""Owner-approved native OpenSSH entry point.

The username occupies the existing approval ledger's credential-file binding,
without reading or storing any credential file. No automatic retries.
"""
from __future__ import annotations

from typing import Any

from .superagent_native_ssh import run_native_ssh_command, _USER
from .superagent_ssh_approvals import prepare_ssh_command, consume_ssh_approval
from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor


def _username(value: str) -> str:
    if not isinstance(value, str) or not _USER.fullmatch(value) or value.startswith("-"):
        raise ValueError("Invalid SSH username")
    return value


def prepare_native_ssh_command(
    policy: RemotePermissionPolicy, actor: VerifiedActor,
    node_id: str, username: str, command: str,
) -> dict[str, Any]:
    """Create a short-lived, exact one-time owner approval; no network I/O."""
    user = _username(username)
    result = prepare_ssh_command(
        policy, actor, node_id, user, command, approval_kind="native-openssh"
    )
    result["username"] = user
    result["transport"] = "native-openssh"
    return result


def execute_approved_native_ssh_command(
    policy: RemotePermissionPolicy, actor: VerifiedActor,
    approval_token: str, node_id: str, username: str, command: str,
    *, timeout: float = 20.0,
) -> dict[str, Any]:
    """Consume approval before any remote operation, even on failed attempts."""
    user = _username(username)
    consume_ssh_approval(
        policy, actor, approval_token, node_id, user, command,
        approval_kind="native-openssh",
    )
    return run_native_ssh_command(
        policy, actor, node_id, user, command, timeout=timeout,
    )


__all__ = ["prepare_native_ssh_command", "execute_approved_native_ssh_command"]
