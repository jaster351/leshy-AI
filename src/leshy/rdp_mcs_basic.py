"""RDP MCS/GCC Basic Settings Exchange framing (MS-RDPBCGR 2.2.1.3-4).

Only for an already authenticated, owner-authorized CredSSP TLS channel.
This codec never sends packets, establishes a desktop, or grants controls.
No static virtual channels: clipboard and drive redirection stay disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

MAX_PACKET = 8192
_GCC_OID = bytes.fromhex("000500147c0001")


class MCSError(ValueError):
    """Malformed or unsupported RDP MCS/GCC settings packet."""


def _length(n: int) -> bytes:
    if n < 0 or n > MAX_PACKET:
        raise MCSError("MCS length is out of bounds")
    if n < 128:
        return bytes([n])
    encoded = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _ber(tag: bytes, data: bytes) -> bytes:
    return tag + _length(len(data)) + data


def _integer(n: int) -> bytes:
    if type(n) is not int or not 0 <= n <= 65535:
        raise MCSError("Invalid MCS domain parameter")
    # The published MS-RDPBCGR example uses unsigned FF FF for 65535.
    return _ber(b"\x02", n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big"))


def _domain(values: tuple[int, ...]) -> bytes:
    if len(values) != 8:
        raise MCSError("MCS DomainParameters requires eight integers")
    return _ber(b"\x30", b"".join(_integer(n) for n in values))


def _per_length(n: int) -> bytes:
    if not 0 <= n <= 0x3FFF:
        raise MCSError("GCC data exceeds PER length limit")
    return bytes([n]) if n < 128 else bytes([0x80 | (n >> 8), n & 255])


def _client_core(width: int, height: int, selected_protocol: int, client_name: str) -> bytes:
    if any(type(n) is not int or not 320 <= n <= 8192 for n in (width, height)):
        raise MCSError("Invalid RDP desktop size")
    if type(selected_protocol) is not int or selected_protocol not in (2, 8):
        raise MCSError("CredSSP or CredSSP-EX is required")
    if (not isinstance(client_name, str) or not client_name or
            len(client_name) > 15 or
            any(ord(c) < 33 or ord(c) > 126 for c in client_name)):
        raise MCSError("Invalid RDP client name")
    name = client_name.encode("utf-16-le").ljust(32, b"\x00")
    fields = b"".join((
        struct.pack("<IHHHHII", 0x00080004, width, height, 0xCA01, 0xAA03, 0x409, 3790),
        name, struct.pack("<III", 4, 0, 12), b"\x00" * 64,
        struct.pack("<HHIHHH", 0xCA01, 1, 0, 24, 7, 1),
        b"\x00" * 64, struct.pack("<BBI", 0, 0, selected_protocol),
    ))
    if len(fields) != 212:
        raise AssertionError("Unexpected RDP client core size")
    return struct.pack("<HH", 0xC001, 216) + fields


def build_mcs_connect_initial(width: int = 1280, height: int = 720,
                              *, selected_protocol: int = 2,
                              client_name: str = "LESHY-AI") -> bytes:
    """Complete TPKT/X.224/MCS Connect-Initial with no virtual channels.

    MUST only be sent after authenticated and authorized CredSSP delegation.
    """
    core = _client_core(width, height, selected_protocol, client_name)
    security = struct.pack("<HHII", 0xC002, 12, 0, 0)  # external TLS only
    network = struct.pack("<HHI", 0xC003, 8, 0)  # no clipboard/drive
    blocks = core + security + network
    gcc_request = bytes.fromhex("000800100001c000") + b"Duca" + _per_length(len(blocks)) + blocks
    gcc = _GCC_OID + _per_length(len(gcc_request)) + gcc_request
    body = b"".join((
        b"\x04\x01\x01\x04\x01\x01\x01\x01\xff",
        _domain((34, 2, 0, 1, 0, 1, 65535, 2)),
        _domain((1, 1, 1, 1, 0, 1, 1056, 2)),
        _domain((65535, 64535, 65535, 1, 0, 1, 65535, 2)),
        _ber(b"\x04", gcc),
    ))
    packet = b"\x02\xf0\x80" + _ber(b"\x7f\x65", body)
    packet = b"\x03\x00" + struct.pack(">H", len(packet) + 4) + packet
    if len(packet) > MAX_PACKET:
        raise MCSError("MCS packet too large")
    return packet


def _read(data: bytes, pos: int, tag: bytes) -> tuple[bytes, int]:
    if data[pos:pos + len(tag)] != tag:
        raise MCSError("Unexpected MCS BER tag")
    pos += len(tag)
    if pos >= len(data):
        raise MCSError("Truncated MCS BER length")
    first = data[pos]
    pos += 1
    if first < 128:
        n = first
    else:
        count = first & 127
        if count not in (1, 2) or pos + count > len(data) or data[pos] == 0:
            raise MCSError("Invalid MCS BER length")
        n = int.from_bytes(data[pos:pos + count], "big")
        if n < 128:
            raise MCSError("Noncanonical MCS BER length")
        pos += count
    if n > MAX_PACKET or pos + n > len(data):
        raise MCSError("Invalid MCS BER bounds")
    return data[pos:pos + n], pos + n


def _per_read(data: bytes, pos: int) -> tuple[int, int]:
    if pos >= len(data):
        raise MCSError("Truncated GCC PER length")
    n = data[pos]
    pos += 1
    if n & 0x80:
        if pos >= len(data):
            raise MCSError("Truncated GCC PER length")
        n = ((n & 0x3F) << 8) | data[pos]
        pos += 1
        if n < 128:
            raise MCSError("Noncanonical GCC PER length")
    return n, pos


@dataclass(frozen=True)
class ServerMCSSettings:
    io_channel_id: int
    static_channels: tuple[int, ...]
    core_version: int
    server_requested_protocols: int | None


def parse_mcs_connect_response(
    packet: bytes, *, expected_requested_protocols: int = 0x0B
) -> ServerMCSSettings:
    """Strict TPKT/MCS/GCC response; reject legacy encryption and channels.

    Returns channel metadata, NOT proof of Windows logon or desktop access.
    """
    if (not isinstance(packet, bytes) or len(packet) < 18 or len(packet) > MAX_PACKET
            or packet[:2] != b"\x03\x00"
            or int.from_bytes(packet[2:4], "big") != len(packet)
            or packet[4:7] != b"\x02\xf0\x80"):
        raise MCSError("Invalid RDP MCS response TPKT/X224")
    body, end = _read(packet, 7, b"\x7f\x66")
    if end != len(packet):
        raise MCSError("Trailing MCS response data")
    result, p = _read(body, 0, b"\x0a")
    if result != b"\x00":
        raise MCSError("MCS connection rejected")
    identifier, p = _read(body, p, b"\x02")
    if not identifier or len(identifier) > 2:
        raise MCSError("Invalid MCS connect identifier")
    parameters, p = _read(body, p, b"\x30")
    at, values = 0, []
    while at < len(parameters):
        value, at = _read(parameters, at, b"\x02")
        if not value or len(value) > 4:
            raise MCSError("Invalid MCS domain parameter")
        values.append(int.from_bytes(value, "big"))
    if len(values) != 8 or values[7] != 2 or values[6] < 1056:
        raise MCSError("Unsupported MCS domain parameters")
    gcc, p = _read(body, p, b"\x04")
    if p != len(body) or len(gcc) < 23 or gcc[:7] != _GCC_OID:
        raise MCSError("Invalid GCC connection data")
    _, offset = _per_read(gcc, 7)  # T.124 response declared length is ignored
    if gcc[offset:offset + 1] != b"\x14":
        raise MCSError("Unexpected GCC conference response")
    offset += 1
    if offset + 12 > len(gcc):
        raise MCSError("Truncated GCC response header")
    node_id = int.from_bytes(gcc[offset:offset + 2], "big") + 1001
    offset += 2
    if (not 1001 <= node_id <= 65536 or
            gcc[offset:offset + 6] != b"\x01\x01\x00\x01\xc0\x00"):
        raise MCSError("Invalid GCC server identity")
    offset += 6
    if gcc[offset:offset + 4] != b"McDn":
        raise MCSError("Unexpected GCC server H.221 key")
    offset += 4
    size, offset = _per_read(gcc, offset)
    if len(gcc) - offset != size:
        raise MCSError("GCC user data length mismatch")
    blocks = gcc[offset:]
    at, found = 0, {}
    while at < len(blocks):
        if at + 4 > len(blocks):
            raise MCSError("Truncated GCC server block")
        block_type, length = struct.unpack_from("<HH", blocks, at)
        if length < 4 or at + length > len(blocks) or block_type in found:
            raise MCSError("Invalid or duplicate GCC server block")
        found[block_type] = blocks[at + 4:at + length]
        at += length
    core, net, sec = (found.get(k) for k in (0x0C01, 0x0C03, 0x0C02))
    if (core is None or net is None or sec is None
            or len(core) < 4 or len(net) < 4 or len(sec) < 8):
        raise MCSError("Missing mandatory GCC server settings")
    if struct.unpack_from("<II", sec, 0) != (0, 0):
        raise MCSError("Legacy RDP encryption is disallowed")
    io_channel, count = struct.unpack_from("<HH", net)
    if count != 0 or len(net) != 4:
        # We sent zero static channels. Do not accept unsolicited mappings.
        raise MCSError("Unexpected server static virtual channels")
    if io_channel < 1001:
        raise MCSError("Invalid server I/O channel identifier")
    requested = struct.unpack_from("<I", core, 4)[0] if len(core) >= 8 else None
    if requested is not None and requested != expected_requested_protocols:
        raise MCSError("Unexpected negotiated client protocols in MCS response")
    return ServerMCSSettings(io_channel, (), struct.unpack_from("<I", core)[0], requested)


__all__ = ["MCSError", "ServerMCSSettings", "build_mcs_connect_initial", "parse_mcs_connect_response"]
