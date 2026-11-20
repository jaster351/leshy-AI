"""Strict RDP Confirm Active builder for the already NLA-bound TLS/MCS channel.

This only sends capability metadata. It does not complete finalization,
instantiate a renderer, enable input, or confirm Windows logon success.
No legacy security header is included under enhanced RDP security.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

_MCS_USER_BASE = 1001
_MAX_PACKET = 8192
_X224 = b"\x02\xf0\x80"
_DESCRIPTOR = b"LESHY\x00"


class RDPConfirmActiveError(ValueError):
    """Untrusted, inconsistent or unsupported RDP capability settings."""


@dataclass(frozen=True)
class ConfirmActiveReceipt:
    share_id: int
    user_channel_id: int
    io_channel_id: int
    sent: bool = True
    finalization_complete: bool = False
    desktop_available: bool = False
    input_enabled: bool = False


def _channel(value: int) -> int:
    if type(value) is not int or not 1001 <= value <= 65535:
        raise RDPConfirmActiveError("Invalid RDP MCS channel")
    return value


def _cap(kind: int, data: bytes) -> bytes:
    if not isinstance(data, bytes) or len(data) > 512:
        raise RDPConfirmActiveError("Invalid RDP capability")
    return struct.pack("<HH", kind, len(data) + 4) + data


def _per_length(n: int) -> bytes:
    if not 1 <= n < 16384:
        raise RDPConfirmActiveError("Invalid PER payload size")
    if n < 128:
        return bytes([n])
    return bytes([0x80 | (n >> 8), n & 0xff])


def _capabilities(width: int, height: int, color_depth: int) -> bytes:
    # The General capability explicitly avoids fast-path, remote-program,
    # salted checksums, encryption extensions and auto-reconnect assertions.
    general = _cap(1, struct.pack("<9HBB", 1, 3, 0x0200, 0, 0, 0, 0, 0, 0, 0, 0))
    # Compression and multi-rectangle are required by MS-RDPBCGR for bitmap
    # capabilities, but these packets do NOT activate image consumption.
    bitmap = _cap(2, struct.pack(
        "<9HBB2H", color_depth, 1, 1, 1, width, height, 0,
        0, 1, 0, 0, 1, 0,
    ))
    # The contents of the Control capability are ignored by the server.
    control = _cap(5, struct.pack("<4H", 0, 0, 2, 2))
    # Default window activation, no special key/window-manager support.
    activation = _cap(7, b"\x00" * 8)
    # No color pointer cache: no decoded cursor path is available yet.
    pointer = _cap(8, struct.pack("<3H", 0, 0, 0))
    # The client's node ID must be zero.
    share = _cap(9, struct.pack("<HH", 0, 0))
    # At least scancode support is mandatory per MS-RDPBCGR. No fast path,
    # touch, mouseX, or Unicode extension advertised. Actual input remains
    # disabled at the owner-authorized RDP session layer.
    input_cap = _cap(13, struct.pack("<HHIIII", 1, 0, 0x0409, 0, 0, 0)
                     + b"\x00" * 64)
    assert len(general) == 24 and len(bitmap) == 28 and len(input_cap) == 88
    return general + bitmap + control + activation + pointer + share + input_cap


def build_confirm_active(
    *, share_id: int, user_channel_id: int, io_channel_id: int,
    width: int = 1280, height: int = 720, color_depth: int = 24,
    server_capability_types: tuple[int, ...],
) -> bytes:
    """Build TPKT/X224/MCS SendDataRequest/TS_CONFIRM_ACTIVE_PDU.

    This is only a serialized protocol message. The server may refuse these
    preliminary capabilities. No decoder or remote input is switched on.
    """
    if type(share_id) is not int or not 1 <= share_id <= 0xffffffff:
        raise RDPConfirmActiveError("Invalid RDP share identifier")
    user, io = _channel(user_channel_id), _channel(io_channel_id)
    if user == io:
        raise RDPConfirmActiveError("User channel cannot equal I/O channel")
    if (type(width) is not int or type(height) is not int
            or not 320 <= width <= 8192 or not 200 <= height <= 8192
            or color_depth not in (16, 24, 32) or type(color_depth) is not int):
        raise RDPConfirmActiveError("Unsupported desktop dimensions or color depth")
    if (not isinstance(server_capability_types, tuple) or
            not {1, 2}.issubset(server_capability_types)):
        raise RDPConfirmActiveError("Server did not advertise General and Bitmap")
    capabilities = _capabilities(width, height, color_depth)
    combined = struct.pack("<HH", 7, 0) + capabilities
    body = (struct.pack("<IHHH", share_id, 1002, len(_DESCRIPTOR), len(combined))
            + _DESCRIPTOR + combined)
    share_header = struct.pack("<HHH", len(body) + 6, 0x13, user)
    # Enhanced RDP security: NO 4-byte SEC header on Confirm Active.
    payload = share_header + body
    mcs = (b"\x64" + struct.pack(">HH", user - _MCS_USER_BASE, io)
           + b"\x70" + _per_length(len(payload)) + payload)
    packet = b"\x03\x00" + struct.pack(">H", 7 + len(mcs)) + _X224 + mcs
    if len(packet) > _MAX_PACKET:
        raise RDPConfirmActiveError("Confirm Active frame exceeds limit")
    return packet


__all__ = ["RDPConfirmActiveError", "ConfirmActiveReceipt", "build_confirm_active"]
