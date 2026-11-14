"""Short-lived, single-use approvals for exact SSH commands.

Approvals are bound to live verified owner, Web session, node, selected
credential file and command digest. Model responses cannot mint an approval.
No raw commands or secrets are persisted in the approval ledger.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any

from .superagent_ssh_terminal import check_terminal_permission
from .superagent_ssh_checked_step import validate_checked_step

_LOCK = threading.RLock()
_PENDING: dict[str, tuple[str, str, str, str, str, str, float]] = {}
_TTL = 90.0
_MAX_PENDING = 128


def _digest(command: str) -> str:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Введите SSH-команду")
    if "\x00" in command or len(command.encode("utf-8")) > 4096:
        raise ValueError("SSH-команда слишком длинная")
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def _file(name: str) -> str:
    if (not isinstance(name, str) or not name or len(name) > 255
            or name in {".", ".."} or any(c in name for c in "/\\:\x00")
            or any(ord(c) < 32 for c in name)):
        raise ValueError("Укажите выбранный файл доступа без пути")
    return name


def prepare_ssh_command(policy: Any, actor: Any, node: str,
                        credential_file: str, command: str, *,
                        approval_kind: str = "terminal-command") -> dict[str, Any]:
    """Preflight rights, produce server-side digest and single-use receipt."""
    normalized_node = check_terminal_permission(policy, actor, node)
    name = _file(credential_file)
    digest = _digest(command)
    if approval_kind not in {"terminal-command", "checked-step"}:
        raise ValueError("Неизвестный тип подтверждения")
    token = secrets.token_urlsafe(32)
    token_key = hashlib.sha256(token.encode("ascii")).hexdigest()
    with _LOCK:
        now = time.monotonic()
        for key, row in list(_PENDING.items()):
            if row[-1] < now:
                _PENDING.pop(key, None)
        if len(_PENDING) >= _MAX_PENDING:
            raise PermissionError("Слишком много неподтверждённых SSH-команд")
        _PENDING[token_key] = (
            actor.owner_id, actor.session_fingerprint, normalized_node,
            name, digest, approval_kind, now + _TTL,
        )
    return {
        "approval_token": token, "sha256": digest, "expires_seconds": 90,
        "node_id": normalized_node, "command_bytes": len(command.encode("utf-8")),
    }


def consume_ssh_approval(policy: Any, actor: Any, token: str, node: str,
                         credential_file: str, command: str, *,
                         approval_kind: str = "terminal-command") -> None:
    """Consume BEFORE remote effects: retries/replays cannot run twice."""
    policy._check_actor(actor)
    if not isinstance(token, str) or not 20 <= len(token) <= 128:
        raise PermissionError("Подтверждение SSH-команды отсутствует")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _LOCK:
        row = _PENDING.pop(token_hash, None)
    if not row or time.monotonic() > row[-1]:
        raise PermissionError("Подтверждение SSH-команды истекло или использовано")
    expected = (
        actor.owner_id, actor.session_fingerprint,
        check_terminal_permission(policy, actor, node),
        _file(credential_file), _digest(command), approval_kind,
    )
    if not all(hmac.compare_digest(str(x), str(y)) for x, y in zip(row[:6], expected)):
        raise PermissionError("Подтверждение выдано для другого действия")
    # The live node key and permissions are ALSO checked inside the executor.


def _checked_step_signature(command: str, verification_command: str,
                            expected_text: str) -> str:
    """Canonical domain-separated digest of BOTH commands and predicate."""
    validate_checked_step(command, verification_command, expected_text)
    canonical = json.dumps(
        {
            "type": "ssh-checked-step-v1",
            "command": command,
            "verification_command": verification_command,
            "expected_text": expected_text,
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return "ssh-checked-step-v1:" + hashlib.sha256(canonical).hexdigest()


def prepare_ssh_checked_step(policy: Any, actor: Any, node: str,
                             credential_file: str, command: str,
                             verification_command: str,
                             expected_text: str = "") -> dict[str, Any]:
    """Prepare one approval for the full two-phase action, not its first command."""
    signature = _checked_step_signature(command, verification_command, expected_text)
    result = prepare_ssh_command(
        policy, actor, node, credential_file, signature,
        approval_kind="checked-step",
    )
    result["step_sha256"] = signature.split(":")[-1]
    result["commands"] = 2
    # The signature contains no raw command output or secret.
    return result


def consume_ssh_checked_approval(policy: Any, actor: Any, token: str, node: str,
                                 credential_file: str, command: str,
                                 verification_command: str,
                                 expected_text: str = "") -> None:
    signature = _checked_step_signature(command, verification_command, expected_text)
    consume_ssh_approval(
        policy, actor, token, node, credential_file, signature,
        approval_kind="checked-step",
    )


__all__ = [
    "prepare_ssh_command", "consume_ssh_approval",
    "prepare_ssh_checked_step", "consume_ssh_checked_approval",
]

