"""Provider-authorized SSH terminal with per-command human approval.

Supports arbitrary SSH commands, OS detection, pinned host-key verification
before password and bounded output. No LLM has direct transport access.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import re
import socket
import time
from typing import Any, Iterator

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_verified_ssh import _credential, _node_ip, _fixed_command

_PIN = re.compile(r"[0-9a-f]{64}\Z")
_OUTPUT_LIMIT = 32768


def _registered_pin(policy: RemotePermissionPolicy, actor: VerifiedActor,
                    node: str, capability: str) -> str:
    """No secret read or outbound TCP connection before authorization."""
    policy._check_actor(actor)
    with policy.storage.connection() as db:
        row = db.execute(
            "SELECT owner_id,host_fingerprint FROM superagent_remote_nodes WHERE node_id=?",
            (node,),
        ).fetchone()
        grant = db.execute(
            "SELECT 1 FROM superagent_remote_permissions WHERE node_id=? AND "
            "grantee_id=? AND capability=? AND expires_at>?",
            (node, actor.owner_id, capability, time.time()),
        ).fetchone()
    if row is None or (row["owner_id"] != actor.owner_id and grant is None):
        raise PermissionError("Компьютер не зарегистрирован либо нет разрешения")
    pin = str(row["host_fingerprint"]).lower()
    if not _PIN.fullmatch(pin):
        raise PermissionError("Не подтверждён SSH-ключ компьютера")
    return pin


def check_terminal_permission(policy: RemotePermissionPolicy, actor: VerifiedActor,
                              node_id: str) -> str:
    node = _node_ip(node_id)
    _registered_pin(policy, actor, node, "execute")
    return node


@contextmanager
def verified_connection(node: str, filename: str, expected_pin: str, policy: RemotePermissionPolicy,
                        actor: VerifiedActor, capability: str, timeout: float):
    """Compare observed SSH host key before the password is transmitted."""
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("Для SSH требуется Paramiko") from exc
    login, password = _credential(policy.storage, actor.owner_id, node, filename)
    sock = None
    transport = None
    try:
        sock = socket.create_connection((node, 22), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        actual = hashlib.sha256(transport.get_remote_server_key().asbytes()).hexdigest()
        if not hmac.compare_digest(actual, expected_pin):
            raise PermissionError("SSH-ключ сервера не совпал с проверенным")
        policy.require(actor, node, capability, observed_fingerprint=actual)
        transport.auth_password(login, password, fallback=False)
        if not transport.is_authenticated():
            raise PermissionError("SSH-вход не подтверждён")
        yield transport, actual
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()


def _detect_remote_os(transport: Any, policy: RemotePermissionPolicy,
                      actor: VerifiedActor, node: str, pin: str) -> str:
    """Detect by actual responses; never trust the prompt's OS description."""
    for command, kind in (("uname -s", "unix"), ("cmd.exe /c ver", "windows")):
        policy.require(actor, node, "execute", observed_fingerprint=pin)
        try:
            code, output = _fixed_command(transport, command, 5.0, None)
        except Exception:
            # A Windows SSH server can reject the Unix probe as an SSH
            # channel exception rather than an exit code. Try Windows next.
            continue
        if code != 0:
            continue
        if kind == "unix" and output in {"Linux", "FreeBSD", "OpenBSD", "NetBSD", "Darwin"}:
            return output
        if kind == "windows" and "windows" in output.casefold():
            return "Windows"
    raise PermissionError("ОС не определена: выполнение команды не разрешено")


def _bounded_exec(transport: Any, command: str, timeout: float) -> dict[str, Any]:
    """Read stdout AND stderr, never block indefinitely on exit status."""
    channel = transport.open_session(timeout=timeout)
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)
        deadline = time.monotonic() + timeout
        stdout, stderr = bytearray(), bytearray()
        while time.monotonic() < deadline:
            moved = False
            for ready, read, target in (
                (channel.recv_ready, channel.recv, stdout),
                (channel.recv_stderr_ready, channel.recv_stderr, stderr),
            ):
                if ready():
                    chunk = read(8192)
                    moved = True
                    remaining = _OUTPUT_LIMIT - len(stdout) - len(stderr)
                    if len(chunk) > remaining:
                        if remaining > 0:
                            target.extend(chunk[:remaining])
                        return {
                            "exit_code": None, "truncated": True,
                            "stdout": stdout.decode("utf-8", errors="replace"),
                            "stderr": stderr.decode("utf-8", errors="replace"),
                        }
                    target.extend(chunk)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                return {
                    "exit_code": int(channel.recv_exit_status()),
                    "truncated": False,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                }
            if not moved:
                time.sleep(0.025)
        raise TimeoutError("SSH-команда превысила время выполнения")
    finally:
        channel.close()


def run_ssh_terminal_command(
    policy: RemotePermissionPolicy, actor: VerifiedActor,
    node_id: str, credential_filename: str, command: str, *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Exec arbitrary command after Web consumes an exact one-time approval.

    This method is NOT a general LLM tool. Use only via the owner-verified
    Web route, with check_terminal_permission + single-use approval.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Команда SSH не задана")
    if "\x00" in command or len(command.encode("utf-8")) > 4096:
        raise ValueError("Команда SSH слишком длинная")
    if not (2 <= float(timeout) <= 30):
        raise ValueError("Недопустимое время SSH-команды")
    node = _node_ip(node_id)
    pin = _registered_pin(policy, actor, node, "execute")
    with verified_connection(
        node, credential_filename, pin, policy, actor, "execute", min(timeout, 10)
    ) as (transport, observed):
        os_family = _detect_remote_os(transport, policy, actor, node, observed)
        policy.require(actor, node, "execute", observed_fingerprint=observed)
        result = _bounded_exec(transport, command, timeout)
    policy.storage.audit("superagent.ssh.command", {
        "node_id": node, "os_family": os_family,
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "exit_code": result["exit_code"], "truncated": result["truncated"],
    })
    return {"node_id": node, "os_family": os_family, **result}


__all__ = ["check_terminal_permission", "run_ssh_terminal_command", "verified_connection"]
