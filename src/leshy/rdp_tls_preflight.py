"""Read-only RDP X.224 and TLS certificate preflight (Windows and Linux).

Per MS-RDPBCGR this is only the negotiation phase, NOT a logged-in RDP session.
No credentials, graphics, keyboard events or CredSSP messages are transmitted.
The expected TLS certificate SHA-256 MUST come from a trusted enrollment path,
never from a first-use value returned by this method.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import re
import socket
import ssl
import struct
import time
from typing import Callable


# MS-RDPBCGR 2.2.1.1.1 protocol identifiers.
_SSL = 0x00000001
_HYBRID = 0x00000002
_HYBRID_EX = 0x00000008
_REQUESTED = _SSL | _HYBRID | _HYBRID_EX
_PROTOCOLS = {_SSL: "tls", _HYBRID: "credssp", _HYBRID_EX: "credssp_early_auth"}
_PIN = re.compile(r"[0-9a-fA-F]{64}\Z")


class RDPPreflightError(RuntimeError):
    """A safe, non-secret summary of a rejected RDP preflight."""


@dataclass(frozen=True)
class RDPPreflightResult:
    security_protocol: str
    tls_certificate_verified: bool
    tls_version: str
    authenticated_desktop: bool = False


def _connection_request() -> bytes:
    """TPKT + X.224 CR TPDU + 8-byte RDP_NEG_REQ, no routing token."""
    negotiation = struct.pack("<BBHI", 1, 0, 8, _REQUESTED)
    x224 = bytes((6 + len(negotiation), 0xE0, 0, 0, 0, 0, 0)) + negotiation
    return struct.pack(">BBH", 3, 0, 4 + len(x224)) + x224


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise RDPPreflightError("RDP negotiation exceeded overall timeout")
    return left


def _recv_exact(stream: socket.socket, size: int, deadline: float) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        stream.settimeout(_remaining(deadline))
        part = stream.recv(remaining)
        if not part:
            raise RDPPreflightError("RDP server closed the negotiation")
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


def _parse_connection_confirm(packet: bytes) -> str:
    """Only a complete, valid X.224 CC with secure RDP_NEG_RSP is accepted."""
    if len(packet) < 11 or packet[:2] != b"\x03\x00":
        raise RDPPreflightError("Invalid RDP TPKT response")
    length = int.from_bytes(packet[2:4], "big")
    if length != len(packet) or length > 512:
        raise RDPPreflightError("Invalid RDP response length")
    data = packet[4:]
    if len(data) != 15 or data[0] != len(data) - 1:
        raise RDPPreflightError("Invalid RDP X.224 confirmation")
    if data[1] != 0xD0 or data[6] != 0:
        raise RDPPreflightError("Invalid RDP X.224 message type")
    kind, _flags, size, selected = struct.unpack("<BBHI", data[7:15])
    if size != 8:
        raise RDPPreflightError("Invalid RDP security negotiation")
    if kind == 3:
        raise RDPPreflightError("RDP server rejected secure protocols")
    if kind != 2 or selected not in _PROTOCOLS:
        # Refuse Standard RDP Security, unknown protocols, and downgrades.
        raise RDPPreflightError("RDP did not negotiate supported TLS security")
    return _PROTOCOLS[selected]


def probe_rdp_tls(
    node_ip: str,
    expected_tls_sha256: str,
    *,
    authorize: Callable[[str], None],
    timeout: float = 5.0,
) -> RDPPreflightResult:
    """Probe TCP/3389 without credentials, requiring an independently pinned cert.

    Caller must provide a server-side policy that revalidates a verified owner,
    registration and the RDP capability for *node_ip*. No user-facing API route
    should invoke this function directly with a caller-provided callback.
    """
    if not callable(authorize):
        raise PermissionError("Verified RDP owner authorization is required")
    try:
        node = str(ipaddress.ip_address(node_ip))
    except ValueError as exc:
        raise ValueError("RDP target must be a literal IP address") from exc
    if not isinstance(expected_tls_sha256, str) or not _PIN.fullmatch(expected_tls_sha256):
        raise PermissionError("Independently enrolled RDP certificate SHA-256 is required")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 15:
        raise ValueError("RDP probe timeout must be between 1 and 15 seconds")
    trusted_pin = expected_tls_sha256.lower()
    authorize(node)
    deadline = time.monotonic() + timeout

    raw = None
    secured = None
    try:
        raw = socket.create_connection((node, 3389), timeout=timeout)
        raw.settimeout(_remaining(deadline))
        raw.sendall(_connection_request())
        header = _recv_exact(raw, 4, deadline)
        if header[:2] != b"\x03\x00":
            raise RDPPreflightError("Invalid RDP TPKT header")
        packet_len = int.from_bytes(header[2:4], "big")
        if packet_len < 11 or packet_len > 512:
            raise RDPPreflightError("Invalid RDP TPKT length")
        protocol = _parse_connection_confirm(header + _recv_exact(raw, packet_len - 4, deadline))

        # RDP servers often use self-signed certificates. Pinning, not an
        # unchecked certificate, is the source of server identity here.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        raw.settimeout(_remaining(deadline))
        secured = context.wrap_socket(raw, server_hostname=None)
        raw = None
        _remaining(deadline)
        certificate = secured.getpeercert(binary_form=True)
        if not isinstance(certificate, bytes) or not certificate:
            raise RDPPreflightError("RDP TLS peer provided no certificate")
        observed = hashlib.sha256(certificate).hexdigest()
        if not hmac.compare_digest(observed, trusted_pin):
            raise PermissionError("RDP TLS server identity does not match enrolled certificate")
        authorize(node)
        # No CredSSP tokens are sent. The authenticated desktop is NOT ready.
        return RDPPreflightResult(
            security_protocol=protocol,
            tls_certificate_verified=True,
            tls_version=str(secured.version() or "unknown"),
        )
    except PermissionError:
        # An identity mismatch or revoked owner permission is not a network error.
        raise
    except (OSError, ssl.SSLError) as exc:
        raise RDPPreflightError("RDP network or TLS negotiation failed") from exc
    finally:
        if secured is not None:
            secured.close()
        if raw is not None:
            raw.close()


__all__ = ["RDPPreflightError", "RDPPreflightResult", "probe_rdp_tls"]
