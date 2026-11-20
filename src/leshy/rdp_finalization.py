"""Conservative RDP/TLS connection-finalization PDUs (MS-RDPBCGR 2.2.1.14-22).

No live graphical session, decoding, or remote input capability is offered by
this codec. Caller must use an owner-verified, pinned TLS/NLA/MCS channel.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .rdp_server_activation import RDPActivationError, read_server_mcs_data

_X224 = b"\x02\xf0\x80"
_SERVER_CHANNEL = 1002
_MAX_PACKET = 16384
_SYNC, _CONTROL, _FONTLIST, _FONTMAP = 0x1f, 0x14, 0x27, 0x28
_ACTION_COOPERATE, _ACTION_REQUEST, _ACTION_GRANTED = 4, 1, 2


class RDPFinalizationError(ValueError):
    """Untrusted or unexpected finalization PDU; never contains secret data."""


@dataclass(frozen=True)
class RDPFinalizationStatus:
    """Protocol controls are granted, but framebuffer and input are disabled."""
    share_id: int
    user_channel_id: int
    io_channel_id: int
    acknowledged_pdus: tuple[str, ...]
    protocol_finalized: bool = True
    desktop_available: bool = False
    input_enabled: bool = False


def _check_id(value: int, name: str) -> int:
    if type(value) is not int or not 1001 <= value <= 65535:
        raise RDPFinalizationError(f"Invalid {name} identifier")
    return value


def _check_share(share_id: int) -> int:
    if type(share_id) is not int or not 0 < share_id <= 0xffffffff:
        raise RDPFinalizationError("Invalid RDP share ID")
    return share_id


def _share_data(share_id: int, source: int, kind: int, body: bytes) -> bytes:
    total = 18 + len(body)
    # In uncompressed client PDUs, the uncompressedLength includes the
    # four-byte T.128 header-prefix, not the full Share Data PDU length.
    return (struct.pack("<HHH", total, 0x0017, source)
            + struct.pack("<IBBHBBH", share_id, 0, 1, len(body) + 4,
                          kind, 0, 0) + body)


def _per_length(length: int) -> bytes:
    if not 0 < length < 0x4000:
        raise RDPFinalizationError("Invalid MCS PER data length")
    if length < 128:
        return bytes((length,))
    return bytes((0x80 | (length >> 8), length & 255))


def _frame(share_id: int, user: int, io: int, kind: int, body: bytes) -> bytes:
    share_id, user, io = _check_share(share_id), _check_id(user, "user"), _check_id(io, "I/O")
    if user == io:
        raise RDPFinalizationError("User channel must differ from I/O channel")
    data = _share_data(share_id, user, kind, body)
    mcs = b"\x64" + struct.pack(">HH", user - 1001, io) + b"\x70" + _per_length(len(data)) + data
    frame = b"\x03\x00" + struct.pack(">H", 7 + len(mcs)) + _X224 + mcs
    if len(frame) > _MAX_PACKET:
        raise RDPFinalizationError("RDP finalization packet too large")
    return frame


def client_finalization_packets(*, share_id: int, user_channel_id: int,
                                io_channel_id: int) -> tuple[bytes, ...]:
    """Client Sync, Cooperate, Request Control, Font List; no cached keys.

    No four-byte legacy SEC header under enhanced TLS; never emit unrelated
    clipboard, drive or input PDUs.
    """
    return (
        _frame(share_id, user_channel_id, io_channel_id, _SYNC,
               struct.pack("<HH", 1, _SERVER_CHANNEL)),
        _frame(share_id, user_channel_id, io_channel_id, _CONTROL,
               struct.pack("<HHI", _ACTION_COOPERATE, 0, 0)),
        _frame(share_id, user_channel_id, io_channel_id, _CONTROL,
               struct.pack("<HHI", _ACTION_REQUEST, 0, 0)),
        _frame(share_id, user_channel_id, io_channel_id, _FONTLIST,
               struct.pack("<HHHH", 0, 0, 3, 50)),
    )


def parse_server_finalization(packet: bytes, *, expected: str,
                              share_id: int, server_user_id: int,
                              user_channel_id: int, io_channel_id: int) -> None:
    """Validate exactly one expected server Sync/Cooperate/Grant/Font Map.

    The caller's state machine must advance only after this succeeds and
    must never infer that a validated Font Map is a decoded desktop frame.
    """
    share_id = _check_share(share_id)
    server_user_id = _check_id(server_user_id, "server")
    user_channel_id = _check_id(user_channel_id, "user")
    io_channel_id = _check_id(io_channel_id, "I/O")
    if expected not in ("synchronize", "cooperate", "granted_control", "font_map"):
        raise RDPFinalizationError("Unknown finalization stage")
    try:
        origin, data = read_server_mcs_data(packet, expected_io_channel=io_channel_id)
    except RDPActivationError as exc:
        raise RDPFinalizationError("Invalid finalization MCS frame") from exc
    if origin != server_user_id or len(data) not in (22, 26):
        raise RDPFinalizationError("Unexpected server MCS identity or PDU size")
    total, kind, source = struct.unpack_from("<HHH", data)
    if total != len(data) or kind != 0x17 or source != server_user_id:
        raise RDPFinalizationError("Invalid finalization share-control header")
    actual_share, _pad, priority, _uncompressed, subtype, compressed, compressed_len = (
        struct.unpack_from("<IBBHBBH", data, 6)
    )
    if (actual_share != share_id or priority not in (0, 1, 2, 4)
            or (priority == 0 and expected != "synchronize")
            or compressed or compressed_len):
        raise RDPFinalizationError("Invalid finalization security/compression metadata")
    body = data[18:]
    if expected == "synchronize":
        if subtype != _SYNC or len(body) != 4 or struct.unpack("<H", body[:2])[0] != 1:
            raise RDPFinalizationError("Invalid server Synchronize")
        # Microsoft documents uninitialized targetUser on some servers.
        # Do not use it as a grant; only sync message type is authoritative.
    elif expected == "font_map":
        if subtype != _FONTMAP or len(body) != 8:
            raise RDPFinalizationError("Invalid server Font Map")
        count, total_count, flags, entry_size = struct.unpack("<4H", body)
        if (count, total_count, flags, entry_size) != (0, 0, 3, 4):
            raise RDPFinalizationError("Unsupported server Font Map")
    else:
        if subtype != _CONTROL or len(body) != 8:
            raise RDPFinalizationError("Invalid server Control")
        action, grant, control = struct.unpack("<HHI", body)
        if expected == "cooperate":
            if (action, grant, control) != (_ACTION_COOPERATE, 0, 0):
                raise RDPFinalizationError("Invalid server Cooperate")
        elif (action, grant, control) != (
            _ACTION_GRANTED, user_channel_id, _SERVER_CHANNEL
        ):
            raise RDPFinalizationError("Invalid server Granted Control")


__all__ = [
    "RDPFinalizationError", "RDPFinalizationStatus",
    "client_finalization_packets", "parse_server_finalization",
]
