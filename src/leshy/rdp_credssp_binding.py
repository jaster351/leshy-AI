"""CredSSP v5+ TLS public-key binding primitives (MS-CSSP 3.1.5).

This module is deliberately NOT a GSS/SPNEGO implementation. Hashes MUST be
sealed/unsealed by the authenticated SPNEGO confidentiality layer before any
TSRequest pubKeyAuth message is sent or trusted. It does not delegate secrets.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets

_CLIENT_MAGIC = b"CredSSP Client-To-Server Binding Hash\x00"
_SERVER_MAGIC = b"CredSSP Server-To-Client Binding Hash\x00"
_PIN = re.compile(r"[0-9a-fA-F]{64}\Z")
_MAX_CERT = 128 * 1024


class RDPBindingError(ValueError):
    """Invalid certificate, failed pin or unsafe CredSSP binding state."""


def _der_element(data: bytes, position: int, *, tag: int | None = None) -> tuple[int, bytes, int]:
    """Read a bounded definite-length DER element, rejecting noncanonical lengths."""
    if position < 0 or position + 2 > len(data):
        raise RDPBindingError("Truncated X.509 DER")
    actual, length_octet = data[position], data[position + 1]
    if tag is not None and actual != tag:
        raise RDPBindingError("Unexpected X.509 DER tag")
    start = position + 2
    if length_octet < 0x80:
        length = length_octet
    else:
        width = length_octet & 0x7F
        if not 1 <= width <= 3 or start + width > len(data) or data[start] == 0:
            raise RDPBindingError("Invalid X.509 DER length")
        length = int.from_bytes(data[start:start + width], "big")
        if length < 128:
            raise RDPBindingError("Noncanonical X.509 DER length")
        start += width
    end = start + length
    if length > _MAX_CERT or end > len(data):
        raise RDPBindingError("X.509 DER length exceeds limit")
    return actual, data[start:end], end


def _exact_sequence(data: bytes) -> bytes:
    _, body, end = _der_element(data, 0, tag=0x30)
    if end != len(data):
        raise RDPBindingError("Trailing X.509 DER content")
    return body


def certificate_subject_public_key(cert_der: bytes) -> bytes:
    """Extract SubjectPublicKey BIT STRING *payload*, not SPKI or certificate DER.

    Does not by itself validate the certificate's identity, chain, signature
    or expiry. Call only after independently verifying the exact TLS cert pin.
    """
    if not isinstance(cert_der, bytes) or not 0 < len(cert_der) <= _MAX_CERT:
        raise RDPBindingError("Invalid RDP TLS certificate")
    certificate = _exact_sequence(cert_der)
    _, tbs, pos = _der_element(certificate, 0, tag=0x30)
    _, _algorithm, pos = _der_element(certificate, pos, tag=0x30)
    _, _signature, pos = _der_element(certificate, pos, tag=0x03)
    if pos != len(certificate):
        raise RDPBindingError("Malformed X.509 certificate envelope")

    pos = 0
    if tbs and tbs[0] == 0xA0:
        _, version, pos = _der_element(tbs, pos, tag=0xA0)
        _, number, end = _der_element(version, 0, tag=0x02)
        if end != len(version) or number not in (b"\x00", b"\x01", b"\x02"):
            raise RDPBindingError("Unsupported X.509 certificate version")
    # TBSCertificate: serialNumber, signature, issuer, validity, subject,
    # subjectPublicKeyInfo; ignore permitted optional fields after the key.
    for tag in (0x02, 0x30, 0x30, 0x30, 0x30):
        _, field, pos = _der_element(tbs, pos, tag=tag)
        if not field:
            raise RDPBindingError("Empty mandatory X.509 field")
    _, spki, pos = _der_element(tbs, pos, tag=0x30)
    key_info = _exact_sequence(b"\x30" + _der_length(len(spki)) + spki)
    _, algorithm, at = _der_element(key_info, 0, tag=0x30)
    if not algorithm:
        raise RDPBindingError("Invalid X.509 key algorithm")
    _, key_bits, at = _der_element(key_info, at, tag=0x03)
    if at != len(key_info) or len(key_bits) < 2 or key_bits[0] != 0:
        raise RDPBindingError("Invalid X.509 SubjectPublicKey BIT STRING")
    return key_bits[1:]


def _der_length(value: int) -> bytes:
    if value < 128:
        return bytes((value,))
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(raw),)) + raw


def _binding_hash(magic: bytes, nonce: bytes, subject_public_key: bytes) -> bytes:
    if not isinstance(nonce, bytes) or len(nonce) != 32:
        raise RDPBindingError("CredSSP v5+ nonce must be exactly 32 bytes")
    if not isinstance(subject_public_key, bytes) or not 0 < len(subject_public_key) <= 16 * 1024:
        raise RDPBindingError("Invalid TLS SubjectPublicKey bytes")
    return hashlib.sha256(magic + nonce + subject_public_key).digest()


def client_to_server_hash(nonce: bytes, subject_public_key: bytes) -> bytes:
    """Value to seal with authenticated SPNEGO before sending pubKeyAuth."""
    return _binding_hash(_CLIENT_MAGIC, nonce, subject_public_key)


def server_to_client_hash(nonce: bytes, subject_public_key: bytes) -> bytes:
    """Value expected after authenticated SPNEGO unseals server pubKeyAuth."""
    return _binding_hash(_SERVER_MAGIC, nonce, subject_public_key)


@dataclass(frozen=True)
class CredSSPBinding:
    nonce: bytes
    client_hash: bytes
    server_hash: bytes

    def verify_unsealed_server_hash(self, unsealed: bytes) -> None:
        """Requires SPNEGO-authenticated GSS_Unwrap result, NOT raw wire bytes."""
        if not isinstance(unsealed, bytes) or not hmac.compare_digest(unsealed, self.server_hash):
            raise PermissionError("CredSSP TLS public-key server binding failed")


def prepare_credssp_binding(cert_der: bytes, expected_tls_sha256: str) -> CredSSPBinding:
    """Bind a fresh nonce to the *pinned* DER certificate of the live TLS peer.

    Passing a hash discovered from the same untrusted connection is insecure.
    Never treat this result as a completed CredSSP login or allow authInfo yet.
    """
    if not isinstance(expected_tls_sha256, str) or not _PIN.fullmatch(expected_tls_sha256):
        raise RDPBindingError("Independently enrolled RDP TLS certificate pin required")
    if not isinstance(cert_der, bytes) or not hmac.compare_digest(
        hashlib.sha256(cert_der).hexdigest(), expected_tls_sha256.lower()
    ):
        raise PermissionError("RDP TLS certificate pin mismatch")
    public_key = certificate_subject_public_key(cert_der)
    nonce = secrets.token_bytes(32)
    return CredSSPBinding(
        nonce=nonce,
        client_hash=client_to_server_hash(nonce, public_key),
        server_hash=server_to_client_hash(nonce, public_key),
    )


__all__ = [
    "CredSSPBinding", "RDPBindingError", "certificate_subject_public_key",
    "client_to_server_hash", "server_to_client_hash", "prepare_credssp_binding",
]
