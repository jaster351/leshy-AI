"""Strict CredSSP v5/v6 TSRequest DER envelope; no credential delegation.

Implements MS-CSSP 2.2.1 and 2.2.1.1. This is a message codec, not an
NTLM/Kerberos mechanism, secure channel, or authenticated desktop session.
The authInfo field is deliberately rejected: a later component must handle
SPNEGO GSS wrapping and server authorization before sending credentials.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_MESSAGE = 256 * 1024
MAX_TOKEN = 128 * 1024
MAX_TOKENS = 8


class TSRequestError(ValueError):
    """Malformed, oversized or unsupported CredSSP message (no raw payload)."""


@dataclass(frozen=True)
class TSRequest:
    version: int = 6
    nego_tokens: tuple[bytes, ...] = ()
    pub_key_auth: bytes | None = None
    error_code: int | None = None
    client_nonce: bytes | None = None


def _length(n: int) -> bytes:
    if n < 128:
        return bytes((n,))
    encoded = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(encoded),)) + encoded


def _tlv(tag: int, data: bytes) -> bytes:
    return bytes((tag,)) + _length(len(data)) + data


def _uint(value: int, *, max_value: int) -> bytes:
    if type(value) is not int or not 0 <= value <= max_value:
        raise TSRequestError("CredSSP integer out of range")
    data = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if data[0] & 0x80:
        data = b"\x00" + data
    return _tlv(0x02, data)


def encode_tsrequest(value: TSRequest) -> bytes:
    """Create canonical DER; accepts opaque SPNEGO tokens, never passwords."""
    if not isinstance(value, TSRequest) or value.version not in (5, 6):
        raise TSRequestError("CredSSP version 5 or 6 is required")
    if not isinstance(value.nego_tokens, tuple) or len(value.nego_tokens) > MAX_TOKENS:
        raise TSRequestError("Too many SPNEGO tokens")
    fields = [_tlv(0xA0, _uint(value.version, max_value=6))]
    if value.nego_tokens:
        items = []
        for token in value.nego_tokens:
            if not isinstance(token, bytes) or not 0 < len(token) <= MAX_TOKEN:
                raise TSRequestError("Invalid SPNEGO token")
            items.append(_tlv(0x30, _tlv(0xA0, _tlv(0x04, token))))
        fields.append(_tlv(0xA1, _tlv(0x30, b"".join(items))))
    if value.pub_key_auth is not None:
        if not isinstance(value.pub_key_auth, bytes) or not 0 < len(value.pub_key_auth) <= MAX_TOKEN:
            raise TSRequestError("Invalid pubKeyAuth")
        fields.append(_tlv(0xA3, _tlv(0x04, value.pub_key_auth)))
    if value.error_code is not None:
        fields.append(_tlv(0xA4, _uint(value.error_code, max_value=0xFFFFFFFF)))
    if value.client_nonce is not None:
        if not isinstance(value.client_nonce, bytes) or len(value.client_nonce) != 32:
            raise TSRequestError("CredSSP nonce must be exactly 32 bytes")
        fields.append(_tlv(0xA5, _tlv(0x04, value.client_nonce)))
    result = _tlv(0x30, b"".join(fields))
    if len(result) > MAX_MESSAGE:
        raise TSRequestError("CredSSP message exceeds size limit")
    return result


def _read(data: bytes, at: int, expected: int | None = None) -> tuple[int, bytes, int]:
    """Read one definite-length canonical DER element."""
    if at + 2 > len(data):
        raise TSRequestError("Truncated DER element")
    tag, first = data[at], data[at + 1]
    if expected is not None and tag != expected:
        raise TSRequestError("Unexpected DER tag")
    pos = at + 2
    if first < 128:
        length = first
    else:
        count = first & 0x7F
        if not 1 <= count <= 3 or pos + count > len(data):
            raise TSRequestError("Invalid DER length encoding")
        if data[pos] == 0:
            raise TSRequestError("Noncanonical DER length")
        length = int.from_bytes(data[pos:pos + count], "big")
        if length < 128:
            raise TSRequestError("Noncanonical DER length")
        pos += count
    if length > MAX_MESSAGE or pos + length > len(data):
        raise TSRequestError("DER element exceeds available data")
    return tag, data[pos:pos + length], pos + length


def _one(data: bytes, tag: int) -> bytes:
    _, content, end = _read(data, 0, tag)
    if end != len(data):
        raise TSRequestError("Unexpected trailing DER data")
    return content


def _number(data: bytes, max_value: int) -> int:
    content = _one(data, 0x02)
    if not content or content[0] & 0x80 or (len(content) > 1 and content[0] == 0 and not content[1] & 0x80):
        raise TSRequestError("Invalid nonnegative DER INTEGER")
    result = int.from_bytes(content, "big")
    if result > max_value:
        raise TSRequestError("CredSSP integer out of range")
    return result


def decode_tsrequest(message: bytes) -> TSRequest:
    """Parse a single complete TSRequest; reject authInfo and legacy versions."""
    if not isinstance(message, bytes) or not 0 < len(message) <= MAX_MESSAGE:
        raise TSRequestError("Invalid CredSSP message size")
    body = _one(message, 0x30)
    at, seen, kwargs = 0, set(), {}
    previous = -1
    while at < len(body):
        tag, content, at = _read(body, at)
        index = tag - 0xA0
        if not 0 <= index <= 5 or index in seen or index <= previous:
            raise TSRequestError("Unexpected or duplicate CredSSP field")
        seen.add(index)
        previous = index
        if index == 0:
            kwargs["version"] = _number(content, 0xFFFFFFFF)
        elif index == 1:
            seq = _one(content, 0x30)
            tokens, pos = [], 0
            while pos < len(seq):
                _, element, pos = _read(seq, pos, 0x30)
                token = _one(_one(element, 0xA0), 0x04)
                if not 0 < len(token) <= MAX_TOKEN or len(tokens) >= MAX_TOKENS:
                    raise TSRequestError("Invalid SPNEGO token")
                tokens.append(token)
            if not tokens:
                raise TSRequestError("Empty NegoData")
            kwargs["nego_tokens"] = tuple(tokens)
        elif index == 2:
            raise TSRequestError("Credential delegation disabled in this codec")
        elif index == 3:
            token = _one(content, 0x04)
            if not 0 < len(token) <= MAX_TOKEN:
                raise TSRequestError("Invalid pubKeyAuth")
            kwargs["pub_key_auth"] = token
        elif index == 4:
            kwargs["error_code"] = _number(content, 0xFFFFFFFF)
        elif index == 5:
            nonce = _one(content, 0x04)
            if len(nonce) != 32:
                raise TSRequestError("Invalid CredSSP nonce")
            kwargs["client_nonce"] = nonce
    if 0 not in seen or kwargs["version"] not in (5, 6):
        raise TSRequestError("Unsupported CredSSP protocol version")
    return TSRequest(**kwargs)


__all__ = ["TSRequest", "TSRequestError", "encode_tsrequest", "decode_tsrequest"]
