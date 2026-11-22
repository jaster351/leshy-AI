"""Single-use, owner/session/ticket/host-bound native RDP launch approvals.

The token only admits ONE launch attempt, not repeated password sends.
The post-consumption callback is a short-lived recheck lease for the SAME
already-approved attempt, including before reading or delegating secrets.
No passwords, authInfo, TLS certificate DER or screen pixels are stored.
"""
from __future__ import annotations

import hashlib
import hmac
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
) -> None:
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
    registered_pin = _registered_pin(policy, actor, node, "rdp")
    policy.require(actor, node, "rdp", observed_fingerprint=registered_pin)
    # Lookup verifies owner's configured credential directory and exact file,
    # but never opens or parses the secret in the approval phase.
    find_credential_file(service.storage, actor.owner_id, filename)
    # Stop or an existing RDP session may race after approval. The trusted
    # factory has its own exclusive registration and live ticket checks.
    if not callable(getattr(service, "start_authorized_native_rdp", None)):
        raise PermissionError("Native RDP service is not available")


def prepare_rdp_approval(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    service: Any,
    ticket_id: str, node_id: str, service_dns_name: str,
    expected_tls_sha256: str, credential_file: str,
) -> dict[str, Any]:
    """Approve review data only; creates no socket and reads no password."""
    scope = _scope(ticket_id, node_id, service_dns_name, expected_tls_sha256, credential_file)
    _validate(policy, actor, service, scope)
    existing_status = getattr(service, "rdp_live_status", None)
    if callable(existing_status) and existing_status(actor, scope[0]).get("registered"):
        raise PermissionError("Для задания уже зарегистрирована RDP-сессия; сначала Stop")
    token = secrets.token_urlsafe(32)
    key = hashlib.sha256(token.encode("ascii")).hexdigest()
    row = (
        actor.owner_id, actor.session_fingerprint,
        *scope,
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
    expected = (actor.owner_id, actor.session_fingerprint, *scope)
    if len(row[0]) != len(expected) or not all(
        hmac.compare_digest(a, b) for a, b in zip(row[0], expected)
    ):
        raise PermissionError("Подтверждение относится к другой Web-сессии или RDP-цели")
    _validate(policy, actor, service, scope)
    lease_expires = time.monotonic() + _LEASE_SECONDS

    def recheck_credential_delegation(ip: str, dns: str) -> None:
        if time.monotonic() > lease_expires:
            raise PermissionError("Время подтверждения передачи RDP-пароля истекло")
        if ip != scope[1] or dns != scope[2]:
            raise PermissionError("Попытка передачи пароля на другой RDP-сервер")
        _validate(policy, actor, service, scope)
    return recheck_credential_delegation


__all__ = ["prepare_rdp_approval", "consume_rdp_approval"]
