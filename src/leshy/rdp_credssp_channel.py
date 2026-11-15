"""Pinned TLS RDP channel for CredSSP binding and separately approved credentials.

Unlike rdp_tls_probe, this keeps the SAME socket through X.224, TLS and
the protected CredSSP TSRequest exchange. A separately owner-approved
internal credential step may follow successful binding. This is NOT a
confirmed Windows logon or an RDP desktop session.

This internal transport has no HTTP, Telegram or LLM entry point. Its
authorize callback must verify a provider-authenticated owner, an enrolled
literal IP + DNS service identity pair, an RDP permission and the TLS pin.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import re
import socket
import ssl
import time
from typing import Callable, Any

from .rdp_tls_probe import _negotiate, _time_left
from .rdp_credssp_tsrequest import MAX_MESSAGE, decode_tsrequest
from .rdp_credssp_negotiator import CredSSPClient
from .rdp_credssp_credentials import (
    RDPCredentials, validate_sealed_authinfo_tsrequest,
)
from .native_spnego import _check_target, new_native_spnego

_CERT_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
_MAX_ROUND_TRIPS = 18


class RDPChannelError(RuntimeError):
    """RDP channel failure without raw wire data or credentials."""


@dataclass(frozen=True)
class RDPBindingStatus:
    selected_protocol: str
    tls_version: str
    pinned_certificate: bool
    server_bound: bool
    credentials_delegated: bool = False
    rdp_desktop_available: bool = False


def _read_exact(tls: ssl.SSLSocket, n: int, deadline: float) -> bytes:
    parts = []
    while n:
        tls.settimeout(_time_left(deadline))
        chunk = tls.recv(n)
        if not chunk:
            raise RDPChannelError("RDP peer closed the protected channel")
        parts.append(chunk)
        n -= len(chunk)
    return b"".join(parts)


def read_tsrequest(tls: ssl.SSLSocket, *, deadline: float) -> bytes:
    """Read exactly ONE canonical DER SEQUENCE, even across fragmented TLS reads."""
    header = _read_exact(tls, 2, deadline)
    if header[0] != 0x30:
        raise RDPChannelError("Unexpected CredSSP DER envelope")
    first = header[1]
    if first < 0x80:
        length = first
    else:
        width = first & 0x7F
        if not 1 <= width <= 3:
            raise RDPChannelError("Invalid CredSSP DER length encoding")
        extra = _read_exact(tls, width, deadline)
        if extra[0] == 0:
            raise RDPChannelError("Noncanonical CredSSP DER length")
        length = int.from_bytes(extra, "big")
        if length < 128:
            raise RDPChannelError("Noncanonical CredSSP DER length")
        header += extra
    if not length or len(header) + length > MAX_MESSAGE:
        raise RDPChannelError("CredSSP frame exceeds size policy")
    packet = header + _read_exact(tls, length, deadline)
    decode_tsrequest(packet)  # Reject malformed or credential-bearing input.
    return packet


class PinnedRDPCredSSPChannel:
    """One owner-scoped TCP/TLS channel with a bounded SPNEGO binding exchange."""

    def __init__(
        self, tls: ssl.SSLSocket, certificate_der: bytes,
        *, ip: str, dns_name: str, pin: str, selected_protocol: str,
        authorize: Callable[[str, str], None], deadline: float,
    ):
        self._tls = tls
        self._certificate = certificate_der
        self._ip, self._dns, self._pin = ip, dns_name, pin
        self._selected, self._authorize_callback = selected_protocol, authorize
        self._deadline = deadline
        self._credssp: CredSSPClient | None = None
        self._closed = False

    def _check(self) -> None:
        if self._closed:
            raise RDPChannelError("RDP channel closed")
        _time_left(self._deadline)
        try:
            self._authorize_callback(self._ip, self._dns)
        except BaseException:
            self.close()
            raise

    @property
    def server_bound(self) -> bool:
        return bool(not self._closed and self._credssp and self._credssp.server_bound)

    @property
    def credentials_delegated(self) -> bool:
        return False

    def _send(self, packet: bytes) -> None:
        self._check()
        if not isinstance(packet, bytes) or not 0 < len(packet) <= MAX_MESSAGE:
            raise RDPChannelError("Invalid outbound CredSSP frame")
        parsed = decode_tsrequest(packet)
        if parsed.error_code is not None:
            raise RDPChannelError("Client cannot transmit CredSSP failure codes")
        self._tls.settimeout(_time_left(self._deadline))
        self._tls.sendall(packet)
        self._check()

    def _receive(self) -> bytes:
        self._check()
        packet = read_tsrequest(self._tls, deadline=self._deadline)
        self._check()
        return packet

    def authenticate_server(self, *, mechanism_factory: Callable[..., Any] | None = None) -> RDPBindingStatus:
        """Perform GSS/SPNEGO + encrypted TLS-public-key proof, WITHOUT authInfo.

        Returns a server-binding status only. The socket stays open inside
        this context manager. It cannot yet perform Windows logon or RDP IO.
        """
        if self._credssp is not None:
            raise RDPChannelError("CredSSP binding already attempted")
        self._check()
        maker = mechanism_factory or new_native_spnego
        mechanism = None
        try:
            mechanism = maker(self._dns, authorize=self._check)
            self._credssp = CredSSPClient(
                mechanism, self._certificate, self._pin, authorize=self._check
            )
            outgoing = self._credssp.start()
            for _ in range(_MAX_ROUND_TRIPS):
                self._send(outgoing)
                incoming = self._receive()
                outgoing = self._credssp.receive(incoming)
                if outgoing is None:
                    if not self._credssp.server_bound:
                        raise RDPChannelError("CredSSP server binding incomplete")
                    self._check()
                    return RDPBindingStatus(
                        self._selected, str(self._tls.version() or "unknown"),
                        pinned_certificate=True, server_bound=True,
                    )
            raise RDPChannelError("CredSSP exchange exceeded round limit")
        except BaseException:
            self.close()
            if mechanism is not None and self._credssp is None:
                mechanism.close()
            raise

    def delegate_password_credentials(
        self, credentials: RDPCredentials, *,
        authorize_delegation: Callable[[str, str], None],
    ) -> RDPBindingStatus:
        """Transmit exactly ONE owner-approved, GSS-encrypted authInfo frame.

        This is an internal API. The owner must explicitly approve supplying
        credentials to the independently pinned RDP host, and the caller must
        already have a trusted local credential source. It does not implement
        Windows logon/MCS, or prove the target accepted the credential.
        """
        if not callable(authorize_delegation):
            raise PermissionError("Separate owner approval for RDP credentials required")
        if (self._credssp is None or not self._credssp.server_bound
                or self._credssp.credentials_delegated):
            raise RDPChannelError("RDP server binding must finish before credential transfer")

        def revalidate_delegation() -> None:
            self._check()
            authorize_delegation(self._ip, self._dns)
            self._check()

        try:
            revalidate_delegation()
            packet = self._credssp.prepare_password_delegation(
                credentials, authorize_delegation=revalidate_delegation,
            )
            # Inbound/ordinary TSRequest decoding STILL forbids authInfo.
            # Only this frame freshly constructed from authenticated GSS Wrap
            # may be sent through the dedicated credential path.
            validate_sealed_authinfo_tsrequest(packet)
            revalidate_delegation()
            self._tls.settimeout(_time_left(self._deadline))
            self._tls.sendall(packet)
            self._check()
            self._credssp.mark_password_sent()
            return RDPBindingStatus(
                self._selected, str(self._tls.version() or "unknown"),
                pinned_certificate=True, server_bound=True,
                credentials_delegated=True, rdp_desktop_available=False,
            )
        except BaseException:
            # Even on send failure the peer might have received authInfo.
            # Never retry automatically and never re-use this GSS channel.
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._credssp is not None:
                self._credssp.close()
        finally:
            self._tls.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def connect_verified_rdp_credssp(
    node_ip: str, service_dns_name: str, enrolled_tls_sha256: str, *,
    authorize: Callable[[str, str], None],
    timeout: float = 15.0,
    port: int = 3389,
) -> PinnedRDPCredSSPChannel:
    """Connect to a pre-enrolled owner-approved IP+DNS SPN+exact TLS cert pin.

    Passing a pin read from THIS connection is forbidden; enrollment must
    occur independently beforehand. On errors all sockets are closed.
    """
    if not callable(authorize):
        raise PermissionError("Verified owner authorization required")
    try:
        ip = str(ipaddress.ip_address(node_ip))
    except ValueError as exc:
        raise ValueError("RDP target must be a literal IP") from exc
    dns = _check_target(service_dns_name)
    if not isinstance(enrolled_tls_sha256, str) or not _CERT_SHA256.fullmatch(enrolled_tls_sha256):
        raise PermissionError("Independently enrolled RDP TLS certificate pin required")
    if (type(timeout) not in (int, float) or not 1 <= timeout <= 30):
        raise ValueError("Invalid total RDP timeout")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid RDP TCP port")
    pin = enrolled_tls_sha256.lower()
    authorize(ip, dns)
    deadline = time.monotonic() + timeout
    raw = None
    tls = None
    try:
        raw = socket.create_connection((ip, port), timeout=_time_left(deadline))
        raw.settimeout(_time_left(deadline))
        selected = _negotiate(raw, deadline=deadline)
        if selected not in ("CredSSP", "CredSSP-EX"):
            raise RDPChannelError("RDP server did not select CredSSP authentication")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        # Exact independently enrolled leaf certificate SHA-256 pin is mandatory.
        context.verify_mode = ssl.CERT_NONE
        raw.settimeout(_time_left(deadline))
        tls = context.wrap_socket(raw, server_hostname=None)
        raw = None
        _time_left(deadline)
        certificate = tls.getpeercert(binary_form=True)
        if not isinstance(certificate, bytes) or not certificate:
            raise PermissionError("RDP server did not supply a TLS certificate")
        if not hmac.compare_digest(hashlib.sha256(certificate).hexdigest(), pin):
            raise PermissionError("RDP TLS certificate fingerprint does not match enrollment")
        authorize(ip, dns)
        _time_left(deadline)
        channel = PinnedRDPCredSSPChannel(
            tls, certificate, ip=ip, dns_name=dns, pin=pin,
            selected_protocol=selected, authorize=authorize, deadline=deadline
        )
        tls = None
        return channel
    except BaseException:
        if tls is not None:
            tls.close()
        if raw is not None:
            raw.close()
        raise


__all__ = [
    "PinnedRDPCredSSPChannel", "RDPBindingStatus", "RDPChannelError",
    "connect_verified_rdp_credssp", "read_tsrequest",
]
