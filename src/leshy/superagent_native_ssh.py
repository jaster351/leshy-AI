"""Opt-in system OpenSSH executor; no passwords or private keys enter Leshy.

This transport is intentionally not exposed as an LLM tool or HTTP endpoint.
The caller must consume a one-time command approval before invoking it.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_ssh_terminal import _registered_pin

_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
_LIMIT = 32768


def run_native_ssh_command(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    node_id: str,
    username: str,
    command: str,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Execute an approved command using system ssh and the user's SSH agent.

    Caller is responsible for single-use human approval. No TOFU, passwords,
    interactive authentication, shell interpolation, or credential files.
    """
    try:
        host = str(ipaddress.ip_address(node_id))
    except ValueError as exc:
        raise ValueError("SSH requires a literal enrolled IP address") from exc
    if not _USER.fullmatch(username or "") or username.startswith("-"):
        raise ValueError("Invalid SSH username")
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise ValueError("Invalid SSH command")
    if len(command.encode("utf-8")) > 4096 or not 2 <= timeout <= 30:
        raise ValueError("Invalid SSH command size or timeout")
    pin = _registered_pin(policy, actor, host, "execute")
    scan = shutil.which("ssh-keyscan")
    ssh = shutil.which("ssh")
    if not scan or not ssh:
        raise RuntimeError("System OpenSSH client and ssh-keyscan are required")

    # ssh-keyscan is untrusted discovery, never authentication.
    scanned = subprocess.run(
        [scan, "-T", "5", "-p", "22", host],
        capture_output=True, timeout=8, check=False,
    )
    if scanned.returncode != 0 and not scanned.stdout:
        raise ConnectionError("Unable to retrieve SSH host key")
    matches = []
    for raw in scanned.stdout.splitlines():
        parts = raw.split()
        if len(parts) < 3 or parts[0].startswith(b"#"):
            continue
        try:
            key = base64.b64decode(parts[2], validate=True)
        except (ValueError, base64.binascii.Error):
            continue
        if hashlib.sha256(key).hexdigest() == pin:
            matches.append(raw)
    if not matches:
        raise PermissionError("SSH host key differs from enrolled fingerprint")

    policy.require(actor, host, "execute", observed_fingerprint=pin)
    with tempfile.TemporaryDirectory(prefix="leshy-ssh-") as directory:
        known_hosts = os.path.join(directory, "known_hosts")
        with open(known_hosts, "wb") as handle:
            handle.write(b"\n".join(matches) + b"\n")
        # Key-only authentication through the user's pre-existing ssh-agent.
        # No private key path, password, or user config is loaded.
        args = [
            ssh, "-F", os.devnull, "-T", "-p", "22",
            "-o", "BatchMode=yes",
            "-o", "PreferredAuthentications=publickey",
            "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no",
            "-o", "PubkeyAuthentication=yes",
            "-o", "IdentitiesOnly=no",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "GlobalKnownHostsFile=" + os.devnull,
            "-o", "UpdateHostKeys=no",
            "-o", "ConnectTimeout=5",
            "-o", "ConnectionAttempts=1",
            "-o", "ControlMaster=no",
            "-o", "ForwardAgent=no",
            "-o", "ClearAllForwardings=yes",
            "-o", "PermitLocalCommand=no",
            "-o", "NumberOfPasswordPrompts=0",
            "-l", username, "--", host, command,
        ]
        try:
            result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("SSH outcome unknown; do not automatically retry") from exc
    output = result.stdout[:_LIMIT]
    error = result.stderr[:_LIMIT]
    policy.storage.audit("superagent.ssh.native_command", {
        "node_id": host,
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "exit_code": result.returncode,
        "truncated": len(result.stdout) > _LIMIT or len(result.stderr) > _LIMIT,
    })
    return {
        "node_id": host, "exit_code": result.returncode,
        "stdout": output.decode("utf-8", errors="replace"),
        "stderr": error.decode("utf-8", errors="replace"),
        "truncated": len(result.stdout) > _LIMIT or len(result.stderr) > _LIMIT,
    }


__all__ = ["run_native_ssh_command"]
