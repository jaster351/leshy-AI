"""Narrow MS-CSSP password credential encoder for an already authenticated channel.

This module never receives untrusted network data or invokes an RDP server.
The only permitted use is a *separately authorized* owner-side CredSSP step
after a pinned TLS peer and GSS-protected server public-key binding.

Raw credentials are never logged, persisted, returned, or interpolated into
a shell command. Python cannot guarantee erasure of immutable input strings;
callers must use a trusted short-lived local credential provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .rdp_credssp_tsrequest import (
    MAX_MESSAGE, MAX_TOKEN, TSRequestError, _number, _one, _read, _tlv, _uint,
)

_CRED_TYPE_PASSWORD = 1
_MAX_FIELD_BYTES = 4096


@dataclass(frozen=True)
class RDPCredentials:
    """Trusted local credential material, never a Web/Telegram request body."""

    username: str
    password: str = field(repr=False)
    domain: str = ""


def _unicode_octets(value: str, *, optional: bool = False) -> bytes:
    if not isinstance(value, str) or (not optional and not value):
        raise ValueError("Missing RDP credential field")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Control characters are not permitted in RDP credentials")
    try:
        encoded = value.encode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        raise ValueError("RDP credential field has invalid Unicode") from exc
    if len(encoded) > _MAX_FIELD_BYTES:
        raise ValueError("RDP credential field exceeds size policy")
    return encoded


def password_credentials_der(value: RDPCredentials) -> bytes:
    """Return TSCredentials(credType=1, credentials=TSPasswordCreds).

    MS-CSSP 2.2.1.2/2.2.1.2.1: domainName, userName and password
    are UTF-16LE OCTET STRINGs in their [0], [1], [2] explicit wrappers.
    This plaintext DER MUST be encrypted with authenticated SPNEGO GSS_Wrap
    before it can ever be placed inside a TSRequest authInfo field.
    """
    if not isinstance(value, RDPCredentials):
        raise TypeError("Trusted local RDP credentials required")
    domain = _unicode_octets(value.domain, optional=True)
    username = _unicode_octets(value.username)
    password = _unicode_octets(value.password)
    inner = _tlv(0x30, b"".join((
        _tlv(0xA0, _tlv(0x04, domain)),
        _tlv(0xA1, _tlv(0x04, username)),
        _tlv(0xA2, _tlv(0x04, password)),
    )))
    outer = _tlv(0x30, (
        _tlv(0xA0, _uint(_CRED_TYPE_PASSWORD, max_value=5))
        + _tlv(0xA1, _tlv(0x04, inner))
    ))
    if len(outer) > 16 * 1024:
        raise ValueError("RDP credentials exceed maximum cleartext size")
    return outer


def sealed_authinfo_tsrequest(sealed: bytes) -> bytes:
    """Encode the *outbound-only* TSRequest(version=6, authInfo=GSS wrapped).

    Deliberately separate from decode_tsrequest: incoming authInfo must always
    remain forbidden, and credential-bearing frames cannot be passed through
    the generic public TSRequest codec.
    """
    if not isinstance(sealed, bytes) or not 0 < len(sealed) <= MAX_TOKEN:
        raise TSRequestError("Invalid encrypted CredSSP credentials")
    message = _tlv(0x30, (
        _tlv(0xA0, _uint(6, max_value=6))
        + _tlv(0xA2, _tlv(0x04, sealed))
    ))
    if len(message) > MAX_MESSAGE:
        raise TSRequestError("CredSSP credential message too large")
    return message


def validate_sealed_authinfo_tsrequest(packet: bytes) -> None:
    """Require exactly a canonical v6 TSRequest containing sealed authInfo.

    This only validates DER framing, NOT the confidentiality of the payload.
    Call solely with output from an authenticated SPNEGO mechanism.wrap().
    """
    if not isinstance(packet, bytes) or not 0 < len(packet) <= MAX_MESSAGE:
        raise TSRequestError("Invalid CredSSP credential message")
    body = _one(packet, 0x30)
    tag, version, at = _read(body, 0, 0xA0)
    if _number(version, 6) != 6:
        raise TSRequestError("CredSSP v6 required for delegation")
    tag, credentials, at = _read(body, at, 0xA2)
    sealed = _one(credentials, 0x04)
    if not 0 < len(sealed) <= MAX_TOKEN or at != len(body):
        raise TSRequestError("Invalid encrypted CredSSP credentials")


__all__ = [
    "RDPCredentials", "password_credentials_der",
    "sealed_authinfo_tsrequest", "validate_sealed_authinfo_tsrequest",
]
