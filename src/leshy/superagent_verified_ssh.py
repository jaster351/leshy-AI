"""Verified read-only SSH OS inspection for a registered SuperAgent node.

No LLM text, IP address, credential file, or SSH password establishes
ownership. Registration and provider-authenticated per-node rights must
already exist; the SSH host key is pinned *before* authentication.
This module never executes user-provided shell commands.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import time
from typing import Any

from .superagent_credential_settings import find_credential_file
from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor


_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_LOGIN = re.compile(r"^[a-zA-Z0-9_.@-]{1,100}$")
_COMMANDS = (("uname -s", "unix"), ("cmd.exe /c ver", "windows"))
_MAX_SECRET_FILE = 8192
_MAX_OUTPUT = 1024


def _node_ip(node_id: str) -> str:
    """Never resolve arbitrary DNS aliases or accept command-like hosts."""
    try:
        ip = ipaddress.ip_address(node_id)
    except ValueError as exc:
        raise ValueError("Для SSH укажите IP зарегистрированного узла") from exc
    if ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_loopback:
        raise ValueError("Недопустимый адрес удалённого узла")
    return str(ip)


def _preauthorize(policy: RemotePermissionPolicy, actor: VerifiedActor, node_id: str) -> str:
    """Check enrollment and identity before opening a socket.

    This initial database check is NOT proof of the live SSH host.
    The actual server public key is checked again through require() before
    the password is sent, and before each remote command.
    """
    policy._check_actor(actor)
    with policy.storage.connection() as conn:
        row = conn.execute(
            "SELECT owner_id,host_fingerprint FROM superagent_remote_nodes WHERE node_id=?",
            (node_id,),
        ).fetchone()
        grant = conn.execute(
            "SELECT 1 FROM superagent_remote_permissions "
            "WHERE node_id=? AND grantee_id=? AND capability='inspect' AND expires_at>?",
            (node_id, actor.owner_id, time.time()),
        ).fetchone()
    if row is None or (row["owner_id"] != actor.owner_id and grant is None):
        raise PermissionError("Узел не зарегистрирован за владельцем или нет разрешения inspect")
    pin = str(row["host_fingerprint"] or "").lower()
    if not _FINGERPRINT.fullmatch(pin):
        raise PermissionError("Для узла не задан проверенный SHA-256 SSH host key")
    return pin


def _credential(storage: Any, owner_id: str, node_id: str, filename: str) -> tuple[str, str]:
    """Read only a tightly bounded owner-selected JSON file, never log it."""
    path = find_credential_file(storage, owner_id, filename)
    old = os.lstat(path)
    if not stat.S_ISREG(old.st_mode):
        raise PermissionError("Файл доступа должен быть обычным файлом")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (old.st_dev, old.st_ino) != (
            opened.st_dev, opened.st_ino
        ) or opened.st_size > _MAX_SECRET_FILE:
            raise PermissionError("Файл доступа изменился или превышает допустимый размер")
        with os.fdopen(fd, "rb", closefd=False) as reader:
            data = reader.read(_MAX_SECRET_FILE + 1)
        if len(data) > _MAX_SECRET_FILE:
            raise PermissionError("Файл доступа слишком большой")
    finally:
        os.close(fd)
    try:
        credentials = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Файл доступа должен содержать UTF-8 JSON") from exc
    if not isinstance(credentials, dict) or set(credentials) != {"host", "username", "password"}:
        raise ValueError("В файле доступа нужны только host, username, password")
    if str(credentials["host"]) != node_id:
        raise PermissionError("Файл доступа предназначен для другого IP")
    login, password = credentials["username"], credentials["password"]
    if not isinstance(login, str) or not _LOGIN.fullmatch(login):
        raise ValueError("Некорректное имя пользователя SSH")
    if not isinstance(password, str) or not 1 <= len(password) <= 2048 or "\x00" in password:
        raise ValueError("Некорректный формат пароля в файле")
    return login, password


def _fixed_command(transport: Any, command: str, timeout: float, cancel_event: Any) -> tuple[int, str]:
    """Bound remote output and the entire command lifetime (no blocking recv_exit_status)."""
    channel = transport.open_session(timeout=timeout)
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)
        deadline = time.monotonic() + timeout
        output = bytearray()
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("SSH-диагностика отменена")
            if channel.recv_ready():
                block = channel.recv(min(256, _MAX_OUTPUT - len(output)))
                output.extend(block)
            if channel.exit_status_ready():
                code = channel.recv_exit_status()
                return code, output.decode("utf-8", errors="replace").strip()
            if len(output) == _MAX_OUTPUT:
                raise RuntimeError("SSH-ответ превысил разрешённый объём")
            time.sleep(0.025)
        raise TimeoutError("SSH-команда не завершилась вовремя")
    finally:
        channel.close()


def _linux_distribution(transport: Any, timeout: float, cancel_event: Any) -> str:
    """Probe only fixed read-only os-release metadata, never shell scripts."""
    try:
        code, result = _fixed_command(transport, "cat /etc/os-release", timeout, cancel_event)
    except (OSError, RuntimeError, TimeoutError):
        return ""
    if code:
        return ""
    values: dict[str, str] = {}
    for line in result.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in {"ID", "VERSION_ID"}:
            continue
        value = value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Za-z0-9._+-]{1,40}", value):
            values[key] = value
    if not values.get("ID"):
        return ""
    return (values["ID"] + (" " + values["VERSION_ID"] if values.get("VERSION_ID") else ""))[:82]


def inspect_remote_os(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    node_id: str,
    credential_filename: str,
    *,
    timeout: float = 5.0,
    cancel_event: Any = None,
) -> dict[str, Any]:
    """Perform a real, read-only SSH inspection against an enrolled IP.

    This method requires externally verified enrollment and per-node
    permission. It cannot enroll a node, grant access or execute arbitrary
    remote commands. All sessions are closed on success, error and cancel.
    """
    node = _node_ip(str(node_id or "").strip())
    if not 1.0 <= float(timeout) <= 10.0:
        raise ValueError("Недопустимый SSH timeout")
    _preauthorize(policy, actor, node)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("SSH-диагностика отменена")
    login, password = _credential(policy.storage, actor.owner_id, node, credential_filename)

    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("Для SSH-диагностики необходим Paramiko") from exc

    sock = None
    transport = None
    try:
        sock = socket.create_connection((node, 22), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        observed = hashlib.sha256(key.asbytes()).hexdigest()
        # Crucial: server key and authorization before *any* credential leaves this host.
        policy.require(actor, node, "inspect", observed_fingerprint=observed)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("SSH-диагностика отменена")
        transport.auth_password(login, password, fallback=False)
        if not transport.is_authenticated():
            raise PermissionError("SSH-аутентификация не подтверждена")
        family, method = "unknown", "ssh:unknown"
        for command, kind in _COMMANDS:
            policy.require(actor, node, "inspect", observed_fingerprint=observed)
            status, output = _fixed_command(transport, command, timeout, cancel_event)
            if status != 0:
                continue
            if kind == "unix" and output in {"Linux", "FreeBSD", "OpenBSD", "NetBSD", "Darwin"}:
                family, method = output, "ssh:uname"
                break
            if kind == "windows" and "windows" in output.casefold():
                family, method = "Windows", "ssh:ver"
                break
        policy.require(actor, node, "inspect", observed_fingerprint=observed)
        distribution = ""
        if family == "Linux":
            # A second fixed read is safe; authorization can be revoked
            # between OS detection and this metadata probe.
            policy.require(actor, node, "inspect", observed_fingerprint=observed)
            distribution = _linux_distribution(transport, timeout, cancel_event)
        result = {
            "node_id": node,
            "os_family": family,
            "distribution": distribution,
            "verified": family != "unknown",
            "method": method,
            "host_key_sha256": observed,
        }
        policy.storage.audit("superagent.ssh.inspect", {
            "node_id": node, "verified": result["verified"], "method": method,
        })
        return result
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()


__all__ = ["inspect_remote_os"]
