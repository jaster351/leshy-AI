"""Interactive SSH enrollment: out-of-band host key + verified live SSH login.

An IP address or an SSH password alone is not accepted. The owner must
obtain a SHA-256 fingerprint independently from the target's trusted console
and the transport must observe that exact key before authenticating.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import socket
import time
from typing import Any

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_verified_ssh import _credential, _node_ip

_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_OPENSSH = re.compile(r"SHA256:([A-Za-z0-9+/]{42,44}={0,2})\Z")
_ATTEMPTS = 3
_PERIOD = 600


def parse_ssh_fingerprint(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("Укажите отпечаток ключа SSH")
    raw = raw.strip()
    if _HEX.fullmatch(raw):
        return raw.lower()
    match = _OPENSSH.fullmatch(raw)
    if match:
        data = match.group(1)
        try:
            decoded = base64.b64decode(data + "=" * (-len(data) % 4), validate=True)
        except (binascii.Error, ValueError):
            decoded = b""
        if len(decoded) == 32 and base64.b64encode(decoded).decode().rstrip("=") == data.rstrip("="):
            return decoded.hex()
    raise ValueError("Нужен SHA256:… из консоли сервера или 64 символа hex")


def _enroll_budget(policy: RemotePermissionPolicy, actor: VerifiedActor) -> None:
    now = time.time()
    with policy.storage.connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS superagent_ssh_enroll_budget "
            "(owner_id TEXT PRIMARY KEY, started_at REAL NOT NULL, attempts INTEGER NOT NULL)"
        )
        row = db.execute(
            "SELECT started_at,attempts FROM superagent_ssh_enroll_budget WHERE owner_id=?",
            (actor.owner_id,),
        ).fetchone()
        if row is None or now - float(row["started_at"]) >= _PERIOD:
            db.execute(
                "INSERT INTO superagent_ssh_enroll_budget VALUES(?,?,1) "
                "ON CONFLICT(owner_id) DO UPDATE SET started_at=excluded.started_at,attempts=1",
                (actor.owner_id, now),
            )
        elif int(row["attempts"]) >= _ATTEMPTS:
            raise PermissionError("Слишком много попыток регистрации; повторите позже")
        else:
            db.execute(
                "UPDATE superagent_ssh_enroll_budget SET attempts=attempts+1 WHERE owner_id=?",
                (actor.owner_id,),
            )


def enroll_verified_ssh_node(
    policy: RemotePermissionPolicy, actor: VerifiedActor, node_id: str,
    credential_filename: str, expected_fingerprint: str, *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Enroll after an out-of-band key match AND successful live authentication."""
    policy._check_actor(actor)
    node = _node_ip(node_id)
    expected = parse_ssh_fingerprint(expected_fingerprint)
    if not (1 <= float(timeout) <= 10):
        raise ValueError("Недопустимое время SSH-подключения")
    with policy.storage.connection() as db:
        row = db.execute(
            "SELECT owner_id,host_fingerprint FROM superagent_remote_nodes WHERE node_id=?",
            (node,),
        ).fetchone()
    if row is not None:
        if row["owner_id"] != actor.owner_id or not hmac.compare_digest(
            str(row["host_fingerprint"]), expected
        ):
            raise PermissionError("Компьютер зарегистрирован другим владельцем или сменил ключ")
        return {"enrolled": True, "already_registered": True, "node_id": node}

    _enroll_budget(policy, actor)
    login, password = _credential(policy.storage, actor.owner_id, node, credential_filename)
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("Компонент SSH не установлен") from exc
    sock = None
    transport = None
    try:
        sock = socket.create_connection((node, 22), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        actual = hashlib.sha256(transport.get_remote_server_key().asbytes()).hexdigest()
        if not hmac.compare_digest(actual, expected):
            raise PermissionError("Ключ SSH-сервера не совпадает с независимо подтверждённым")
        policy._check_actor(actor)
        transport.auth_password(login, password, fallback=False)
        if not transport.is_authenticated():
            raise PermissionError("Права входа SSH не подтверждены")
        # One-use, server-owned verifier exists only after the live proof.
        proof = secrets.token_urlsafe(32)
        registering_policy = RemotePermissionPolicy(
            policy.identity,
            enrollment_verifier=lambda owner, host, fingerprint, offered: (
                owner == actor.owner_id and host == node
                and hmac.compare_digest(str(fingerprint), actual)
                and hmac.compare_digest(str(offered), proof)
            ),
        )
        registering_policy.register_verified_node(
            actor, node, actual, enrollment_proof=proof
        )
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()
    policy.storage.audit("superagent.ssh.enrolled", {"node_id": node, "verified": True})
    return {"enrolled": True, "already_registered": False, "node_id": node}


__all__ = ["parse_ssh_fingerprint", "enroll_verified_ssh_node"]
