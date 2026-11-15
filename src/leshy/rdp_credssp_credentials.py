"""One-time, opt-in CredSSP TSPasswordCreds encoding.

Credential secrets must be supplied by a trusted owner-specific secret
provider *after* pinned TLS + GSS mutual authentication + public-key binding.
Never log, persist, or accept these credentials from an LLM-generated tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Shared canonical ASN.1 DER builders; the public TSRequest decoder still
# rejects authInfo, as remote peers must never send credentials to Leshy.
from .rdp_credssp_tsrequest import _tlv, _uint, MAX_MESSAGE


class CredentialEncodingError(ValueError):
    """Invalid credential metadata; contains no raw secret values."""


@dataclass(repr=False)
class RDPPasswordSecret:
    """Mutable UTF-16LE password bytes obtained only from an owner vault.

    The invoking code MUST call wipe() in a finally block. Python and native
    GSS mechanisms may temporarily copy data; no complete memory-erasure
    guarantee is possible with Python immutable bytes.
    """
    domain: str
    username: str
    password_utf16le: bytearray = field(repr=False)

    def wipe(self) -> None:
        if isinstance(self.password_utf16le, bytearray):
            self.password_utf16le[:] = b"\x00" * len(self.password_utf16le)


def _name_bytes(value: str, *, field_name: str, optional: bool = False) -> bytes:
    if not isinstance(value, str) or len(value) > 256 or (not optional and not value):
        raise CredentialEncodingError(f"Invalid {field_name} length")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise CredentialEncodingError(f"Invalid {field_name} characters")
    try:
        return value.encode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        raise CredentialEncodingError(f"Invalid {field_name} Unicode") from exc


def encode_password_credentials(secret: RDPPasswordSecret) -> bytes:
    """Encode MS-CSSP TSCredentials credType=1, still UNENCRYPTED.

    Never transmit this output directly. Only GSS/SSPI confidentiality wraps
    the complete DER payload, and then send it inside TSRequest.authInfo.
    """
    if not isinstance(secret, RDPPasswordSecret):
        raise CredentialEncodingError("Trusted owner credential source required")
    domain = _name_bytes(secret.domain, field_name="domain", optional=True)
    username = _name_bytes(secret.username, field_name="username")
    password = secret.password_utf16le
    if (not isinstance(password, bytearray) or not 2 <= len(password) <= 2048
            or len(password) % 2):
        raise CredentialEncodingError("Invalid mutable password buffer")
    try:
        if "\x00" in password.decode("utf-16-le", errors="strict"):
            raise CredentialEncodingError("NUL in password is not accepted")
    except UnicodeError as exc:
        raise CredentialEncodingError("Invalid password encoding") from exc
    pw = _tlv(0x30, b"".join((
        _tlv(0xA0, _tlv(0x04, domain)),
        _tlv(0xA1, _tlv(0x04, username)),
        _tlv(0xA2, _tlv(0x04, bytes(password))),
    )))
    result = _tlv(0x30, _tlv(0xA0, _uint(1, max_value=6)) + _tlv(0xA1, _tlv(0x04, pw)))
    if len(result) > MAX_MESSAGE:
        raise CredentialEncodingError("Credential encoding exceeds policy")
    return result


def encode_sealed_authinfo(sealed: bytes) -> bytes:
    """CredSSP TSRequest v6 with ONLY an encrypted authInfo OCTET STRING.

    Must be called on GSS/SSPI Wrap output from a completed, authenticated
    context. In particular, never pass plaintext TSCredentials here.
    """
    if not isinstance(sealed, bytes) or not 0 < len(sealed) <= 128 * 1024:
        raise CredentialEncodingError("Invalid sealed CredSSP token")
    packet = _tlv(0x30, _tlv(0xA0, _uint(6, max_value=6)) + _tlv(0xA2, _tlv(0x04, sealed)))
    if len(packet) > MAX_MESSAGE:
        raise CredentialEncodingError("CredSSP frame exceeds policy")
    return packet


__all__ = ["RDPPasswordSecret", "CredentialEncodingError", "encode_password_credentials", "encode_sealed_authinfo"]
