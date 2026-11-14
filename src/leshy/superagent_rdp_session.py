"""Fail-closed lifecycle for future native RDP transports.

No RDP protocol is implemented here. A backend must independently verify
the target RDP TLS certificate and credentials. This controller never uses
an SSH host key as proof of RDP server identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable, Protocol


@dataclass(frozen=True)
class RDPFrame:
    width: int
    height: int
    pixels: bytes


class RDPBackend(Protocol):
    """Explicitly trusted native implementation; not a shell or launcher."""
    def connect(self, host: str, *, expected_tls_sha256: str) -> object: ...
    def capture(self, connection: object) -> RDPFrame: ...
    def click(self, connection: object, x: int, y: int) -> None: ...
    def type_text(self, connection: object, text: str) -> None: ...
    def close(self, connection: object) -> None: ...


@dataclass(frozen=True)
class FrameReceipt:
    session_id: str
    frame_digest: str
    width: int
    height: int


@dataclass
class _Session:
    owner_id: str
    node_id: str
    connection: object
    frame_digest: str = ""
    captured_at: float = 0.0
    width: int = 0
    height: int = 0
    used: bool = True


class RDPSessionController:
    """Single-process session broker. No HTTP/LLM entry point until a backend exists.

    The injected authorization must independently validate a live provider
    identity, owner, enrolled node, and per-node RDP grant on EVERY operation.
    """
    def __init__(
        self,
        backend: RDPBackend,
        *,
        authorize: Callable[[str, str], None],
        clock: Callable[[], float] = time.monotonic,
        max_frame_age: float = 5.0,
        max_sessions: int = 4,
    ):
        if not callable(authorize) or not (0 < max_frame_age <= 30):
            raise ValueError("Invalid RDP authorization/session policy")
        if not 1 <= max_sessions <= 16:
            raise ValueError("Invalid session limit")
        self.backend = backend
        self.authorize = authorize
        self.clock = clock
        self.max_frame_age = max_frame_age
        self.max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}

    def open(self, owner_id: str, node_id: str, expected_tls_sha256: str) -> str:
        # Enrollment of the TLS identity must happen out of band.
        if not isinstance(expected_tls_sha256, str) or len(expected_tls_sha256) != 64:
            raise PermissionError("An independently enrolled RDP TLS fingerprint is required")
        try:
            bytes.fromhex(expected_tls_sha256)
        except ValueError as exc:
            raise PermissionError("Invalid RDP TLS fingerprint") from exc
        self.authorize(owner_id, node_id)
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError("RDP session limit reached")
            # Backend must fail closed for untrusted TLS certificates.
            conn = self.backend.connect(node_id, expected_tls_sha256=expected_tls_sha256.lower())
            try:
                self.authorize(owner_id, node_id)
            except BaseException:
                self.backend.close(conn)
                raise
            sid = secrets.token_urlsafe(24)
            self._sessions[sid] = _Session(owner_id, node_id, conn)
            return sid

    def _get(self, owner_id: str, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None or not hmac.compare_digest(session.owner_id, owner_id):
            raise PermissionError("RDP session unavailable")
        self.authorize(owner_id, session.node_id)
        return session

    def observe(self, owner_id: str, session_id: str) -> FrameReceipt:
        receipt, _ = self.observe_frame(owner_id, session_id)
        return receipt

    def observe_frame(self, owner_id: str, session_id: str) -> tuple[FrameReceipt, bytes]:
        """In-process frame transfer only; never expose pixels to diagnostics."""
        with self._lock:
            session = self._get(owner_id, session_id)
            frame = self.backend.capture(session.connection)
            # Capture may block; recheck permissions before returning any pixels.
            self.authorize(owner_id, session.node_id)
            if not (1 <= frame.width <= 8192 and 1 <= frame.height <= 8192):
                raise ValueError("Invalid framebuffer dimensions")
            if not isinstance(frame.pixels, bytes) or not (0 < len(frame.pixels) <= 32 * 1024 * 1024):
                raise ValueError("Empty or oversized framebuffer")
            # Frame bytes are never stored in session state or audits.
            digest = hashlib.sha256(
                secrets.token_bytes(16) + frame.width.to_bytes(4, "big") +
                frame.height.to_bytes(4, "big") + frame.pixels
            ).hexdigest()
            session.frame_digest = digest
            session.captured_at = self.clock()
            session.width, session.height = frame.width, frame.height
            session.used = False
            return FrameReceipt(session_id, digest, frame.width, frame.height), frame.pixels

    def click(
        self, owner_id: str, session_id: str, *,
        frame_digest: str, x: int, y: int,
    ) -> None:
        with self._lock:
            session = self._get(owner_id, session_id)
            if (session.used or self.clock() - session.captured_at > self.max_frame_age
                    or not isinstance(frame_digest, str)
                    or not hmac.compare_digest(session.frame_digest, frame_digest)):
                raise PermissionError("RDP frame stale or already used; observe again")
            if type(x) is not int or type(y) is not int or not (0 <= x < session.width and 0 <= y < session.height):
                raise ValueError("Click outside observed framebuffer")
            # Consume first, so failures cannot cause implicit retries.
            session.used = True
            self.authorize(owner_id, session.node_id)
            self.backend.click(session.connection, x, y)

    def type_text(
        self, owner_id: str, session_id: str, *,
        frame_digest: str, text: str,
    ) -> None:
        """Bounded printable text, never arbitrary key combinations."""
        if not isinstance(text, str) or not (1 <= len(text) <= 512):
            raise ValueError("Text length must be 1–512 characters")
        if any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError("Control characters are not accepted")
        with self._lock:
            session = self._get(owner_id, session_id)
            if (session.used or self.clock() - session.captured_at > self.max_frame_age
                    or not isinstance(frame_digest, str)
                    or not hmac.compare_digest(session.frame_digest, frame_digest)):
                raise PermissionError("RDP frame stale or already used; observe again")
            send = getattr(self.backend, "type_text", None)
            if not callable(send):
                raise RuntimeError("RDP backend does not implement text input")
            session.used = True
            self.authorize(owner_id, session.node_id)
            send(session.connection, text)

    def close(self, owner_id: str, session_id: str) -> None:
        # Closing is always permitted for the original session owner, even
        # after a grant is revoked. It cannot send remote input.
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not hmac.compare_digest(session.owner_id, owner_id):
                raise PermissionError("RDP session unavailable")
            del self._sessions[session_id]
            self.backend.close(session.connection)


__all__ = ["RDPBackend", "RDPFrame", "FrameReceipt", "RDPSessionController"]
