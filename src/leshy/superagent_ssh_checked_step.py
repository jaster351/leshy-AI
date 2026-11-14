"""Execute an owner-approved SSH action and inspect its postcondition.

The action and the verification command share one pinned-key SSH transport.
Verification is explicit, never inferred from exit code 0 of the action.
No model may call this module without Web authorization and a single-use
approval bound to *both* exact commands and the expected output predicate.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_ssh_terminal import (
    _bounded_exec, _detect_remote_os, _registered_pin, verified_connection,
)
from .superagent_verified_ssh import _node_ip


def validate_checked_step(command: str, verification_command: str,
                          expected_text: str) -> None:
    """Bound every part of a two-phase SSH action before approval or network."""
    for value, label in (
        (command, "Команда"),
        (verification_command, "Команда проверки"),
    ):
        if (not isinstance(value, str) or not value.strip()
                or "\x00" in value or len(value.encode("utf-8")) > 4096):
            raise ValueError(label + " должна содержать от 1 до 4096 байт")
    if (not isinstance(expected_text, str) or "\x00" in expected_text
            or len(expected_text.encode("utf-8")) > 512):
        raise ValueError("Ожидаемый текст должен быть не длиннее 512 байт")


def _safe_step_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Never mistake an interrupted, truncated or non-zero command for success."""
    return {
        "exit_code": raw.get("exit_code"),
        "truncated": raw.get("truncated") is True,
        "stdout": str(raw.get("stdout") or "")[:32768],
        "stderr": str(raw.get("stderr") or "")[:32768],
    }


def run_ssh_checked_step(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    node_id: str,
    credential_filename: str,
    command: str,
    verification_command: str,
    expected_text: str = "",
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Perform one command, then verify its observable postcondition.

    A failed/unfinished action never starts verification. The verification
    command can itself modify state, so the Web approval must show both
    commands verbatim; this module makes no claim of external fact checking.
    """
    validate_checked_step(command, verification_command, expected_text)
    if type(timeout) not in (int, float) or not 2 <= timeout <= 30:
        raise ValueError("Недопустимый таймаут SSH")
    node = _node_ip(node_id)
    pin = _registered_pin(policy, actor, node, "execute")
    with verified_connection(
        node, credential_filename, pin, policy, actor, "execute", min(timeout, 10)
    ) as (transport, observed):
        os_family = _detect_remote_os(transport, policy, actor, node, observed)
        # A revoked execute permission immediately stops the workflow.
        policy.require(actor, node, "execute", observed_fingerprint=observed)
        primary = _safe_step_result(_bounded_exec(transport, command, timeout))
        check: dict[str, Any] | None = None
        verdict = "action_failed"
        if primary["exit_code"] == 0 and not primary["truncated"]:
            # Revocation, timeout or a dropped channel during post-check
            # means UNKNOWN outcome, not failure and certainly not success.
            try:
                # Recheck rights immediately before the second SSH command.
                policy.require(actor, node, "execute", observed_fingerprint=observed)
                check = _safe_step_result(
                    _bounded_exec(transport, verification_command, timeout)
                )
            except Exception:
                verdict = "verification_unavailable"
            else:
                if check["exit_code"] == 0 and not check["truncated"]:
                    output = check["stdout"] + "\n" + check["stderr"]
                    verdict = "confirmed" if (
                        not expected_text or expected_text in output
                    ) else "not_confirmed"
                else:
                    verdict = "not_confirmed"

    # Only non-sensitive digests and statuses are persisted. The exact
    # commands and outputs are sent only to the authorized Web response.
    policy.storage.audit("superagent.ssh.checked_step", {
        "node_id": node,
        "os_family": os_family,
        "action_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "check_sha256": hashlib.sha256(
            verification_command.encode("utf-8")
        ).hexdigest(),
        "verdict": verdict,
        "action_exit_code": primary["exit_code"],
        "check_exit_code": check["exit_code"] if check is not None else None,
    })
    return {
        "node_id": node,
        "os_family": os_family,
        "verdict": verdict,
        "action": primary,
        "verification": check,
        "expected_text_matched": verdict == "confirmed",
    }


__all__ = ["validate_checked_step", "run_ssh_checked_step"]
