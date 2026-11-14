"""Read-only, credential-free RDP X.224 / pinned-TLS network preflight.

Uses only Python's standard library on Windows and Unix. It deliberately
stops after TLS verification: no CredSSP, login, graphics or input is sent.
Callers must authorize the owner and registered endpoint separately.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import socket
import ssl
import struct

# Request TLS, CredSSP and CredSSP with early authorization. Never offer
# legacy PROTOCOL_RDP without authenticated server TLS identity.
_REQUESTED = 0x0000000B
_TLS_PROTOCOLS = {1: "TLS", 2: "CredSSP", 8: "CredSSP-EX"}
_X224_REQUEST = (
    b"\x03\x00\x00\x13\x0e\xe0\x00\x00\x00\x00\x00"
    + struct.pack("<BBHI", 1, 0, 8, _REQUESTED)
)


@dataclass(frozen=True)
class RDPTLSProbeResult:
    node_ip: str
    selected_protocol: str
    tls_version: str
    certificate_sha256: str
    authenticated: bool = True
    rdp_login_complete: bool = False
    rdp_desktop_available: bool = False


def _exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    while size:
        part = sock.recv(size)
        if not part:
            raise ConnectionError("RDP peer closed during X.224 negotiation")
        chunks.append(part)
        size -= len(part)
    return b"".join(chunks)


def _negotiate(sock: socket.socket) -> str:
    sock.sendall(_X224_REQUEST)
    tpkt = _exact(sock, 4)
    if tpkt[:2] != b"\x03\x00":
        raise ConnectionError("Invalid RDP TPKT header")
    length = struct.unpack(">H", tpkt[2:])[0]
    if not 11 <= length <= 256:
        raise ConnectionError("Invalid RDP negotiation packet length")
    body = _exact(sock, length - 4)
    if len(body) < 7 or body[0] != len(body) - 1 or body[1] != 0xD0:
        raise ConnectionError("Invalid X.224 connection confirmation")
    neg = body[7:]
    if len(neg) != 8:
        raise ConnectionError("Missing or invalid enhanced RDP security response")
    kind, flags, size, selection = struct.unpack("<BBHI", neg)
    if size != 8:
        raise ConnectionError("Invalid RDP negotiation response length")
    if kind == 3:
        raise ConnectionError(f"RDP security negotiation rejected (code {selection})")
    if kind != 2 or selection not in _TLS_PROTOCOLS:
        raise ConnectionError("RDP server did not select a supported TLS security protocol")
    return _TLS_PROTOCOLS[selection]


def probe_rdp_tls(node_ip: str, expected_tls_sha256: str, *, port: int = 3389,
                  timeout: float = 5.0) -> RDPTLSProbeResult:
    """Validate the exact server certificate for an enrolled, owned RDP host.

    No password, user name or NLA token is ever transmitted. The raw TLS
    socket is closed after reading the certificate. It is **not** a client
    implementing complete RDP authentication or remote desktop access.
    """
    try:
        host = str(ipaddress.ip_address(node_ip))
    except ValueError as exc:
        raise ValueError("RDP target must be a literal IPv4/IPv6 address") from exc
    if not isinstance(expected_tls_sha256, str) or len(expected_tls_sha256) != 64:
        raise PermissionError("Expected RDP TLS SHA-256 pin is required")
    try:
        bytes.fromhex(expected_tls_sha256)
    except ValueError as exc:
        raise PermissionError("Invalid RDP TLS SHA-256 pin") from exc
    if type(port) is not int or not (1 <= port <= 65535):
        raise ValueError("Invalid RDP port")
    if not isinstance(timeout, (int, float)) or not (0.1 <= timeout <= 15.0):
        raise ValueError("Invalid RDP timeout")

    with closing(socket.create_connection((host, port), timeout=timeout)) as raw:
        raw.settimeout(timeout)
        selected = _negotiate(raw)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        # Deliberately accept self-signed certs only until the immediately
        # following constant-time exact DER SHA-256 pin comparison.
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with context.wrap_socket(raw, server_hostname=None) as protected:
            peer_der = protected.getpeercert(binary_form=True)
            if not peer_der:
                raise PermissionError("RDP TLS server certificate is missing")
            observed = hashlib.sha256(peer_der).hexdigest()
            if not hmac.compare_digest(observed, expected_tls_sha256.lower()):
                raise PermissionError("RDP TLS certificate fingerprint mismatch")
            return RDPTLSProbeResult(host, selected, protected.version() or "unknown", observed)


__all__ = ["RDPTLSProbeResult", "probe_rdp_tls"]
