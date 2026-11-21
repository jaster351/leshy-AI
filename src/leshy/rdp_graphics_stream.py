"""Bounded, cancellable TPKT reader and policies for owner-verified RDP graphics.

This does not create an RDP session, permit input or send network packets.
Only callers with a completed and independently authorized TLS/NLA channel
may use it. One packet is consumed under a fresh idle deadline.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import socket
import time
from typing import Callable, Protocol

MAX_TPKT_SIZE = 16384
_POLL_SECONDS = 0.2


class RDPGraphicsStreamError(RuntimeError):
    """Safe stream transport error without any packet or credential contents."""


class RDPGraphicsStopped(RDPGraphicsStreamError):
    """Trusted owner explicitly stopped the graphics stream."""


class RDPGraphicsTimeout(RDPGraphicsStreamError):
    """No complete graphics packet arrived within the bounded idle deadline."""


class _TLSSocket(Protocol):
    def settimeout(self, timeout: float) -> None: ...
    def recv(self, amount: int) -> bytes: ...


def _seconds(value: float, minimum: float, maximum: float, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid RDP {label}")
    return float(value)


@dataclass(frozen=True)
class GraphicsStreamPolicy:
    max_duration: float = 300.0
    idle_timeout: float = 60.0
    max_updates: int = 2048
    min_frame_interval: float = 0.1

    def __post_init__(self) -> None:
        _seconds(self.max_duration, 1, 3600, "stream duration")
        _seconds(self.idle_timeout, 0.5, 600, "packet idle timeout")
        _seconds(self.min_frame_interval, 0, 10, "frame interval")
        if type(self.max_updates) is not int or not 1 <= self.max_updates <= 10000:
            raise ValueError("Invalid RDP update limit")


@dataclass(frozen=True)
class GraphicsStreamResult:
    received_updates: int
    published_frames: int
    reason: str
    elapsed_seconds: float
    desktop_verified: bool = False
    input_enabled: bool = False


def read_graphics_tpkt(
    tls: _TLSSocket,
    *,
    deadline: float,
    reauthorize: Callable[[], None],
    stopped: Callable[[], bool],
) -> bytes:
    """Consume exactly one bounded TPKT with frequent stop/revoke polling.

    The caller supplies a per-packet monotonic deadline, never the old
    CredSSP handshake deadline. Partial TLS messages are not reused when
    interrupted; the caller must close the entire connection on errors.
    """
    if not callable(reauthorize) or not callable(stopped):
        raise TypeError("RDP stream requires verified access and stop callbacks")
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("Invalid RDP packet deadline")

    def exact(count: int) -> bytes:
        parts: list[bytes] = []
        while count:
            if stopped():
                raise RDPGraphicsStopped("RDP graphics stopped by owner")
            reauthorize()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RDPGraphicsTimeout("RDP graphics receive timed out")
            tls.settimeout(min(_POLL_SECONDS, remaining))
            try:
                chunk = tls.recv(count)
            except (socket.timeout, TimeoutError):
                continue
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > count:
                raise RDPGraphicsStreamError("RDP TLS stream closed or invalid")
            parts.append(chunk)
            count -= len(chunk)
        return b"".join(parts)

    header = exact(4)
    size = int.from_bytes(header[2:4], "big")
    if header[:2] != b"\x03\x00" or not 16 <= size <= MAX_TPKT_SIZE:
        raise RDPGraphicsStreamError("Invalid or oversized RDP graphics TPKT")
    packet = header + exact(size - 4)
    if stopped():
        raise RDPGraphicsStopped("RDP graphics stopped by owner")
    reauthorize()
    if time.monotonic() >= deadline:
        raise RDPGraphicsTimeout("RDP graphics receive timed out")
    return packet


__all__ = [
    "MAX_TPKT_SIZE", "GraphicsStreamPolicy", "GraphicsStreamResult",
    "RDPGraphicsStreamError", "RDPGraphicsStopped", "RDPGraphicsTimeout",
    "read_graphics_tpkt",
]
