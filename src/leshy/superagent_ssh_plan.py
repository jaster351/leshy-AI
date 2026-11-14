"""Ordered SSH plan runner with fail-stop postcondition verification.

The entire exact sequence must have an owner/session/node-bound one-use
approval from the Web route. No LLM, background worker or DAG tool can call
this as a substitute for approval. A plan may never silently retry steps.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Callable

from .superagent_ssh_checked_step import validate_checked_step, run_ssh_checked_step
from .superagent_ssh_terminal import check_terminal_permission
from .superagent_verified_ssh import _node_ip


MAX_STEPS = 6
MAX_SERIALIZED_BYTES = 32000
# Plans cannot overlap in this process, even on separate nodes. Individual
# terminal commands are separate endpoints and should be treated as concurrent
# external changes, NOT as transactions.
_RUN_LOCK = threading.Lock()


def validate_ssh_plan(steps: Any) -> list[dict[str, str]]:
    """Strict ordered plan shape; never accept code, credentials or extra keys."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError("SSH-план должен содержать от 1 до 6 шагов")
    normalized: list[dict[str, str]] = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {
            "command", "verification_command", "expected_text"
        }:
            raise ValueError("Каждый шаг должен содержать команду и отдельную проверку")
        command, verification, expected = (
            step["command"], step["verification_command"], step["expected_text"]
        )
        validate_checked_step(command, verification, expected)
        normalized.append({
            "command": command, "verification_command": verification,
            "expected_text": expected,
        })
    encoded = json.dumps(normalized, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_SERIALIZED_BYTES:
        raise ValueError("SSH-план превышает 32 КБ")
    return normalized


def plan_approval_signature(steps: Any) -> str:
    """Versioned digest includes order, BOTH command bodies and postconditions."""
    normalized = validate_ssh_plan(steps)
    canonical = json.dumps({
        "domain": "leshy-ssh-plan-v1",
        "steps": normalized,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "ssh-plan-v1:" + hashlib.sha256(canonical).hexdigest()


def execute_approved_ssh_plan(
    policy: Any, actor: Any, node_id: str, credential_filename: str,
    steps: Any, *, may_continue: Callable[[], bool] | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Run approved steps in order, stopping on any non-confirmed or unknown.

    Each step opens an independently authenticated pinned-key SSH connection.
    A successful step is *only* one whose explicit postcondition succeeded.
    Never resume automatically after a disconnect; previously sent commands
    may have affected the target.
    """
    normalized = validate_ssh_plan(steps)
    node = _node_ip(node_id)
    check_terminal_permission(policy, actor, node)
    if type(timeout) not in (int, float) or not 2 <= timeout <= 20:
        raise ValueError("Недопустимый таймаут шага")
    if not _RUN_LOCK.acquire(blocking=False):
        raise PermissionError("Уже выполняется другой SSH-план; дождитесь завершения")
    outcomes: list[dict[str, Any]] = []
    status = "completed"
    stopped_at: int | None = None
    try:
        for index, step in enumerate(normalized):
            try:
                # A later owner logout, role revocation or module shutdown
                # must interrupt the sequence before starting another step.
                policy._check_actor(actor)
                if may_continue is not None and not may_continue():
                    status = "stopped"
                    stopped_at = index
                    break
                check_terminal_permission(policy, actor, node)
            except (PermissionError, ValueError):
                status = "stopped"
                stopped_at = index
                break
            try:
                result = run_ssh_checked_step(
                    policy, actor, node, credential_filename,
                    step["command"], step["verification_command"],
                    step["expected_text"], timeout=timeout,
                )
            except Exception:
                # This could be AFTER the command was accepted remotely.
                # Treat as unknown, never start the next step or auto-retry.
                status = "uncertain"
                stopped_at = index
                outcomes.append({
                    "index": index, "verdict": "unknown",
                    "error": "SSH-соединение или проверка прервались; "
                             "команда могла изменить сервер.",
                })
                break
            outcomes.append({
                "index": index, "verdict": result["verdict"],
                "os_family": result["os_family"],
                "action": result["action"],
                "verification": result["verification"],
            })
            if result["verdict"] != "confirmed":
                status = (
                    "uncertain" if result["verdict"] == "verification_unavailable"
                    else "stopped"
                )
                stopped_at = index
                break
    finally:
        _RUN_LOCK.release()

    # No full commands, expected text, raw stdout/stderr or credentials in audit.
    policy.storage.audit("superagent.ssh.plan", {
        "node_id": node,
        "plan_sha256": plan_approval_signature(normalized).split(":")[-1],
        "status": status,
        "confirmed_steps": sum(x["verdict"] == "confirmed" for x in outcomes),
        "stopped_at": stopped_at,
        "total": len(normalized),
    })
    return {
        "node_id": node, "status": status, "total": len(normalized),
        "confirmed_steps": sum(x["verdict"] == "confirmed" for x in outcomes),
        "stopped_at": stopped_at,
        "steps": outcomes,
        "remaining_not_started": len(normalized) - len(outcomes),
        "auto_retry": False,
    }


__all__ = [
    "MAX_STEPS", "validate_ssh_plan", "plan_approval_signature",
    "execute_approved_ssh_plan",
]
