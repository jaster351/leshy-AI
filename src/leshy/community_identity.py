"""Owner-held Ed25519 identities for future opt-in internet cluster transport.

This module does not enable internet networking or authorize remote execution.
Keys stay under the local LeshyBrain directory and are never sent to peers.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


_MAX_ENVELOPE_BYTES = 64 * 1024
_MAX_AGE_SECONDS = 90
_MAX_FUTURE_SECONDS = 15
_MAX_REPLAY_ENTRIES = 10000
_ALLOWED_KINDS = frozenset({"cluster.hello", "cluster.heartbeat", "cluster.job.offer"})


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Некорректное представление открытого ключа или подписи")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ValueError("Некорректная base64-подпись") from exc
    if _encode(raw) != value:
        raise ValueError("Неканоническая кодировка base64")
    return raw


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class CommunityIdentity:
    """One private key per local user, not a shared secret or a server-side key."""

    def __init__(self, brain_dir: Path | str, owner_id: str):
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("Требуется локальный идентификатор владельца")
        root = Path(brain_dir) / "cluster" / "community-keys"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.key_path = root / (sha256(owner_id.encode("utf-8")).hexdigest() + ".ed25519")
        self._key = self._load_or_create()
        self.public_key = _encode(self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        ))
        self.key_id = sha256(_decode(self.public_key)).hexdigest()

    def _load_or_create(self) -> Ed25519PrivateKey:
        try:
            raw = self.key_path.read_bytes()
        except FileNotFoundError:
            raw = secrets.token_bytes(32)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(self.key_path, flags, 0o600)
            except FileExistsError:
                raw = self.key_path.read_bytes()
            else:
                try:
                    with os.fdopen(fd, "wb") as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
                except Exception:
                    self.key_path.unlink(missing_ok=True)
                    raise
        if len(raw) != 32:
            raise ValueError("Повреждён локальный приватный ключ; автоматическая замена запрещена")
        if os.name == "posix":
            mode = self.key_path.stat().st_mode & 0o777
            if mode & 0o077:
                raise PermissionError("Локальный приватный ключ доступен другим пользователям")
        return Ed25519PrivateKey.from_private_bytes(raw)

    def sign(self, kind: str, payload: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
        if kind not in _ALLOWED_KINDS or not isinstance(payload, dict):
            raise ValueError("Недопустимый тип или содержимое сообщения")
        envelope = {
            "protocol": 1,
            "kind": kind,
            "public_key": self.public_key,
            "timestamp": int(time.time()) if now is None else int(now),
            "nonce": secrets.token_hex(16),
            "payload": payload,
        }
        raw = _canonical(envelope)
        if len(raw) > _MAX_ENVELOPE_BYTES:
            raise ValueError("Превышен лимит сообщения")
        envelope["signature"] = _encode(self._key.sign(raw))
        return envelope


class ReplayGuard:
    """Per-process replay prevention; shared/multiprocess admission will need central storage."""

    def __init__(self):
        self._seen: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._lock = threading.RLock()

    def claim(self, key_id: str, nonce: str, timestamp: int, *, now: int) -> None:
        token = (key_id, nonce)
        with self._lock:
            for candidate, seen_at in list(self._seen.items()):
                if seen_at < now - _MAX_AGE_SECONDS - _MAX_FUTURE_SECONDS:
                    self._seen.pop(candidate, None)
            if token in self._seen:
                raise ValueError("Повтор ранее обработанного сообщения")
            self._seen[token] = timestamp
            while len(self._seen) > _MAX_REPLAY_ENTRIES:
                self._seen.popitem(last=False)


def verify_envelope(
    envelope: dict[str, Any], *, replay_guard: ReplayGuard | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify identity, signature, freshness and replay; NOT authorization.

    The caller must independently authorize the peer and the operation and use
    authenticated, encrypted transport before sending private payloads.
    """
    if not isinstance(envelope, dict):
        raise ValueError("Ожидается подписанное сообщение")
    expected = {"protocol", "kind", "public_key", "timestamp", "nonce", "payload", "signature"}
    if set(envelope) != expected or envelope["protocol"] != 1:
        raise ValueError("Неизвестный формат подписанного сообщения")
    if envelope["kind"] not in _ALLOWED_KINDS or not isinstance(envelope["payload"], dict):
        raise ValueError("Недопустимая операция")
    timestamp = envelope["timestamp"]
    current = int(time.time()) if now is None else int(now)
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ValueError("Некорректная временная метка")
    if timestamp < current - _MAX_AGE_SECONDS or timestamp > current + _MAX_FUTURE_SECONDS:
        raise ValueError("Устаревшее сообщение или неверные часы")
    nonce = envelope["nonce"]
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("Некорректный nonce")
    key_bytes = _decode(envelope["public_key"])
    signature = _decode(envelope["signature"])
    if len(key_bytes) != 32 or len(signature) != 64:
        raise ValueError("Неверная длина открытого ключа или подписи")
    unsigned = {name: envelope[name] for name in expected if name != "signature"}
    raw = _canonical(unsigned)
    if len(raw) > _MAX_ENVELOPE_BYTES:
        raise ValueError("Превышен лимит сообщения")
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, raw)
    except InvalidSignature as exc:
        raise ValueError("Подпись не прошла проверку") from exc
    key_id = sha256(key_bytes).hexdigest()
    if replay_guard is not None:
        replay_guard.claim(key_id, nonce, timestamp, now=current)
    return {"key_id": key_id, "kind": envelope["kind"], "payload": envelope["payload"]}


__all__ = ["CommunityIdentity", "ReplayGuard", "verify_envelope"]
