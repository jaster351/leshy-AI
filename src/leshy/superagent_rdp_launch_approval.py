"""Single-use, owner/session/ticket/host-bound native RDP launch approvals.

The token only admits ONE launch attempt, not repeated password sends.
The post-consumption callback is a short-lived recheck lease for the SAME
already-approved attempt, including before reading or delegating secrets.
No passwords, authInfo, TLS certificate DER or screen pixels are stored.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import stat
import re
import secrets
import threading
import time
from typing import Any, Callable

from .module_settings import module_enabled
from .native_spnego import _check_target
from .superagent_credential_settings import find_credential_file
from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_ssh_terminal import _registered_pin
from .superagent_verified_ssh import _node_ip

_PIN = re.compile(r"[0-9a-fA-F]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,254}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{30,128}\Z")
_LOCK = threading.RLock()
_PENDING: dict[str, tuple[tuple[str, ...], float]] = {}
_TTL = 90.0
_LEASE_SECONDS = 75.0
_MAX_PENDING = 128


def _credential_identity(storage: Any, owner_id: str, filename: str) -> tuple[str, ...]:
    """Bind consent to a specific small regular local file, not only its name.

    No contents are read during preparation. File metadata is *not*
    cryptographic content identity; the guarded reader still validates
    symlinks/inode after open, and the consent callback rechecks this stamp
    directly before the one-way CredSSP credential delegation.
    """
    path = find_credential_file(storage, owner_id, filename)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 8192:
        raise PermissionError("RDP credential file is not a small regular file")
    return tuple(str(x) for x in (
        info.st_dev, info.st_ino, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    ))


def _scope(
    ticket_id: str, node_id: str, service_dns_name: str,
    expected_tls_sha256: str, credential_file: str,
) -> tuple[str, ...]:
    if type(ticket_id) is not str or not ticket_id.startswith("SA-") or len(ticket_id) > 100:
        raise ValueError("Укажите одно действующее задание SuperAgent")
    node = _node_ip(node_id)
    dns = _check_target(service_dns_name)
    if type(expected_tls_sha256) is not str or not _PIN.fullmatch(expected_tls_sha256):
        raise ValueError("Укажите отдельно подтверждённый SHA-256 сертификата RDP")
    if (type(credential_file) is not str or not _NAME.fullmatch(credential_file)
            or not credential_file.lower().endswith(".json")
            or credential_file in {".", ".."}):
        raise ValueError("Выберите локальный файл JSON без пути")
    return ticket_id, node, dns, expected_tls_sha256.lower(), credential_file


def _validate(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    service: Any,
    scope: tuple[str, ...],
    *,
    check_file: bool = True,
) -> str:
    if not isinstance(actor, VerifiedActor) or actor.method != "web":
        raise PermissionError("Требуется подтверждённая Web-сессия владельца")
    policy._check_actor(actor)
    if not module_enabled(service.storage, "superagent") or not module_enabled(
        service.storage, "remote-admin"
    ):
        raise PermissionError("Удалённое управление выключено")
    ticket_id, node, _dns, _pin, filename = scope
    ticket = service.get(ticket_id, owner_id=actor.owner_id)
    if ticket["state"] != "working":
        raise PermissionError("RDP разрешён только для работающего задания")
    request_text = ticket.get("request_text")
    if (type(request_text) is not str or not 1 <= len(request_text) <= 1200
            or str(ticket.get("id", ticket_id)) != ticket_id):
        raise PermissionError("Нужна подтверждённая исходная задача владельца")
    # A Web session becoming verified later must not retroactively upgrade
    # a maintenance ticket explicitly denied at creation.
    mandate = ticket.get("mandate", {})
    # No privilege upgrade for an anonymous/old task that was verified only
    # later; a generic task or tutorial does not authorize native RDP.
    from .superagent import _rdp_explicit_task
    if (not _rdp_explicit_task(request_text)
            or not isinstance(mandate, dict)
            or mandate.get("remote_authorized_at_creation") is not True):
        raise PermissionError("RDP требуется явно разрешить при создании задания")
    source_hash = hashlib.sha256(
        (ticket_id + "\0" + request_text + "\0" +
         str(ticket.get("created_at", ""))).encode("utf-8")
    ).hexdigest()
    registered_pin = _registered_pin(policy, actor, node, "rdp")
    policy.require(actor, node, "rdp", observed_fingerprint=registered_pin)
    # Lookup verifies owner's configured credential directory and exact file,
    # but never opens or parses the secret in the approval phase.
    if check_file:
        _credential_identity(service.storage, actor.owner_id, filename)
    # Stop or an existing RDP session may race after approval. The trusted
    # factory has its own exclusive registration and live ticket checks.
    if not callable(getattr(service, "start_authorized_native_rdp", None)):
        raise PermissionError("Native RDP service is not available")
    return source_hash


def prepare_rdp_approval(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    service: Any,
    ticket_id: str, node_id: str, service_dns_name: str,
    expected_tls_sha256: str, credential_file: str,
) -> dict[str, Any]:
    """Approve review data only; creates no socket and reads no password."""
    scope = _scope(ticket_id, node_id, service_dns_name, expected_tls_sha256, credential_file)
    source_hash = _validate(policy, actor, service, scope, check_file=False)
    file_stamp = _credential_identity(service.storage, actor.owner_id, scope[4])
    existing_status = getattr(service, "rdp_live_status", None)
    if callable(existing_status) and existing_status(actor, scope[0]).get("registered"):
        raise PermissionError("Для задания уже зарегистрирована RDP-сессия; сначала Stop")
    token = secrets.token_urlsafe(32)
    key = hashlib.sha256(token.encode("ascii")).hexdigest()
    row = (
        actor.owner_id, actor.session_fingerprint,
        *scope,
        source_hash,
        *file_stamp,
    )
    with _LOCK:
        now = time.monotonic()
        for old, item in tuple(_PENDING.items()):
            if item[1] <= now:
                del _PENDING[old]
        if len(_PENDING) >= _MAX_PENDING:
            raise PermissionError("Превышен лимит неподтверждённых подключений")
        _PENDING[key] = (row, now + _TTL)
    return {
        "approval_token": token,
        "expires_seconds": int(_TTL),
        "ticket_id": scope[0], "node_id": scope[1],
        "service_dns_name": scope[2],
        "rdp_tls_sha256": scope[3],
        "credential_file": scope[4],
        "credentials_will_be_delegated": True,
        "input_enabled": False,
    }


def consume_rdp_approval(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    service: Any,
    token: str,
    ticket_id: str, node_id: str, service_dns_name: str,
    expected_tls_sha256: str, credential_file: str,
) -> Callable[[str, str], None]:
    """Atomically consume BEFORE network, return one attempt's approval lease."""
    if not isinstance(actor, VerifiedActor) or actor.method != "web":
        raise PermissionError("Подтверждение требует действующей Web-сессии")
    policy._check_actor(actor)
    if type(token) is not str or not _TOKEN.fullmatch(token):
        raise PermissionError("Одноразовое подтверждение RDP отсутствует")
    scope = _scope(ticket_id, node_id, service_dns_name, expected_tls_sha256, credential_file)
    key = hashlib.sha256(token.encode("ascii")).hexdigest()
    with _LOCK:
        row = _PENDING.pop(key, None)
    if row is None or row[1] <= time.monotonic():
        raise PermissionError("Одноразовое подтверждение истекло или использовано")
    source_hash = _validate(policy, actor, service, scope, check_file=False)
    current_stamp = _credential_identity(service.storage, actor.owner_id, scope[4])
    expected = (
        actor.owner_id, actor.session_fingerprint, *scope, source_hash,
        *current_stamp,
    )
    if len(row[0]) != len(expected) or not all(
        hmac.compare_digest(a, b) for a, b in zip(row[0], expected)
    ):
        raise PermissionError("Подтверждение относится к другой Web-сессии или RDP-цели")
    lease_expires = time.monotonic() + _LEASE_SECONDS

    def recheck_credential_delegation(ip: str, dns: str) -> None:
        if time.monotonic() > lease_expires:
            raise PermissionError("Время подтверждения передачи RDP-пароля истекло")
        if ip != scope[1] or dns != scope[2]:
            raise PermissionError("Попытка передачи пароля на другой RDP-сервер")
        fresh_hash = _validate(policy, actor, service, scope, check_file=False)
        fresh_stamp = _credential_identity(service.storage, actor.owner_id, scope[4])
        if (not hmac.compare_digest(fresh_hash, source_hash)
                or fresh_stamp != current_stamp):
            raise PermissionError("Исходное RDP-задание или файл доступа изменились")
    return recheck_credential_delegation


def cancel_rdp_approval(
    policy: RemotePermissionPolicy, actor: VerifiedActor, token: str,
) -> bool:
    """Cancel an unconsumed Web approval without remote side effects.

    Only the *same verified owner and browser session* can revoke this
    token; cross-owner attempts cannot burn somebody else's consent.
    A token already consumed for a running native session is not a Stop.
    """
    if not isinstance(actor, VerifiedActor) or actor.method != "web":
        raise PermissionError("Нужна подтверждённая Web-сессия")
    policy._check_actor(actor)
    if type(token) is not str or not _TOKEN.fullmatch(token):
        raise PermissionError("Некорректное одноразовое подтверждение")
    key = hashlib.sha256(token.encode("ascii")).hexdigest()
    with _LOCK:
        row = _PENDING.get(key)
        if row is None:
            return False
        identity = row[0]
        if (not hmac.compare_digest(identity[0], actor.owner_id)
                or not hmac.compare_digest(identity[1], actor.session_fingerprint)):
            return False
        del _PENDING[key]
        return True


__all__ = ["prepare_rdp_approval", "consume_rdp_approval", "cancel_rdp_approval"]
