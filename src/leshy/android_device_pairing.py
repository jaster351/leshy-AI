"""Owner-approved Android device enrollment. No owner/API token is sent to phones.

Pending requests are ephemeral; approved device token hashes persist in LeshyBrain.
Enrollment requires a valid P-256 proof-of-possession AND a separately verified
Web owner confirmation. Every Android chat request remains conversation-only.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


_CONTEXT = b"leshy-satellite-pair-v1\x00"
_TTL = 300
_MAX_PENDING = 40
_FILE = "android_paired_devices.json"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str, *, maximum: int = 512) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("Invalid base64url value")
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in value):
        raise ValueError("Invalid base64url alphabet")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass
class _Pending:
    public_key: bytes
    fingerprint: str
    name: str
    address: str
    challenge: bytes
    expires: float
    proven: bool = False
    approved: bool = False
    token: str = ""


class AndroidPairing:
    def __init__(self, brain_dir: Path) -> None:
        self._path = Path(brain_dir) / "secrets" / _FILE
        self._lock = threading.RLock()
        self._pending: dict[str, _Pending] = {}

    def _devices(self) -> dict:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if raw.get("schema") == 1 and isinstance(raw.get("devices"), dict):
                return raw["devices"]
        except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
            pass
        return {}

    def _save(self, devices: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"schema": 1, "devices": devices}, ensure_ascii=True)
        temp = self._path.with_name(self._path.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            handle = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self._path)
        finally:
            temp.unlink(missing_ok=True)

    def _prune(self) -> None:
        now = time.monotonic()
        for request_id, request in tuple(self._pending.items()):
            if request.expires <= now:
                del self._pending[request_id]

    @staticmethod
    def _public_key(value: str) -> tuple[bytes, ec.EllipticCurvePublicKey]:
        raw = _decode(value, maximum=384)
        if len(raw) not in range(80, 160):
            raise ValueError("Invalid P-256 public key encoding")
        key = serialization.load_der_public_key(raw)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("Only P-256 public keys are supported")
        canonical = key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if canonical != raw:
            raise ValueError("Public key is not canonical")
        return raw, key

    def begin(self, payload: dict, address: str) -> dict:
        if set(payload) != {"public_key", "name"}:
            raise ValueError("Expected public_key and name")
        public_key, _ = self._public_key(payload["public_key"])
        name = str(payload["name"]).strip()
        if not 1 <= len(name) <= 64 or any(ord(c) < 32 or ord(c) > 126 for c in name):
            # Device labels are informational only, not Web authorization input.
            name = "Android device"
        address = str(address)[:96]
        with self._lock:
            self._prune()
            if (len(self._pending) >= _MAX_PENDING or
                    sum(item.address == address for item in self._pending.values()) >= 3):
                raise PermissionError("Too many pending devices; retry later")
            request_id = secrets.token_urlsafe(24)
            challenge = secrets.token_bytes(32)
            self._pending[request_id] = _Pending(
                public_key, hashlib.sha256(public_key).hexdigest(), name, address,
                challenge, time.monotonic() + _TTL,
            )
            return {
                "request_id": request_id,
                "challenge": _encode(challenge),
                "expires_in": _TTL,
            }

    def _request(self, request_id: str) -> _Pending:
        self._prune()
        if not isinstance(request_id, str) or len(request_id) > 80:
            raise ValueError("Invalid pairing request")
        request = self._pending.get(request_id)
        if request is None:
            raise ValueError("Pairing request expired or not found")
        return request

    def _proof(self, request: _Pending, signature: str) -> None:
        raw = _decode(signature, maximum=256)
        key = serialization.load_der_public_key(request.public_key)
        try:
            key.verify(raw, _CONTEXT + request.challenge, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise PermissionError("Invalid device signature") from exc

    def prove(self, payload: dict) -> dict:
        if set(payload) != {"request_id", "signature"}:
            raise ValueError("Expected request_id and signature")
        with self._lock:
            request = self._request(payload["request_id"])
            self._proof(request, payload["signature"])
            request.proven = True
            return {"state": "pending_owner", "expires_in": max(0, int(request.expires - time.monotonic()))}

    def owner_request(self, request_id: str) -> dict:
        with self._lock:
            request = self._request(request_id)
            if not request.proven:
                raise PermissionError("Device proof not completed")
            return {
                "request_id": request_id,
                "name": request.name,
                "fingerprint": request.fingerprint,
                "state": "approved" if request.approved else "pending_owner",
                "expires_in": max(0, int(request.expires - time.monotonic())),
            }

    def approve(self, request_id: str, owner_id: str) -> dict:
        if not owner_id:
            raise PermissionError("Verified owner required")
        with self._lock:
            request = self._request(request_id)
            if not request.proven:
                raise PermissionError("Device identity proof required")
            if request.approved:
                return {"state": "approved"}
            token = secrets.token_urlsafe(32)
            record_id = secrets.token_hex(16)
            devices = self._devices()
            devices[record_id] = {
                "sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
                "owner_id": owner_id,
                "name": request.name,
                "fingerprint": request.fingerprint,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save(devices)
            request.token = token
            request.approved = True
            return {"state": "approved"}

    def poll(self, payload: dict) -> dict:
        if set(payload) != {"request_id", "signature"}:
            raise ValueError("Expected request_id and signature")
        with self._lock:
            request = self._request(payload["request_id"])
            self._proof(request, payload["signature"])
            if not request.proven:
                raise PermissionError("Device proof not completed")
            if not request.approved:
                return {
                    "state": "pending_owner",
                    "expires_in": max(0, int(request.expires - time.monotonic())),
                }
            # Possession of the P-256 signing key is required to retrieve this.
            return {"state": "approved", "conversation_key": request.token}

    def authorize(self, bearer: str) -> str | None:
        if not bearer.startswith("Bearer "):
            return None
        token = bearer[7:]
        if not 32 <= len(token) <= 128 or not token.isascii() or any(c.isspace() for c in token):
            return None
        hashed = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._lock:
            for record in self._devices().values():
                fingerprint = str(record.get("sha256") or "")
                if len(fingerprint) == 64 and hmac.compare_digest(fingerprint, hashed):
                    return str(record.get("owner_id") or "") or None
        return None

    def list_devices(self, owner_id: str) -> list[dict]:
        with self._lock:
            return [
                {"id": key, "name": value.get("name", "Android device"),
                 "fingerprint": value.get("fingerprint", ""),
                 "created_at": value.get("created_at", "")}
                for key, value in self._devices().items()
                if value.get("owner_id") == owner_id
            ]

    def revoke(self, owner_id: str, device_id: str) -> bool:
        with self._lock:
            devices = self._devices()
            if device_id not in devices or devices[device_id].get("owner_id") != owner_id:
                return False
            del devices[device_id]
            self._save(devices)
            return True
