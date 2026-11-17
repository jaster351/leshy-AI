"""MCS Erect Domain, Attach User and sequential Channel Join (MS-RDPBCGR).

Small, bounded TPKT/X.224/PER packets. No channels except user and I/O are
accepted; this codec does not authorize an RDP desktop or handle input.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct


class MCSChannelError(ValueError):
    """Invalid MCS channel handshake or a rejected/unmatched confirmation."""


_X224_DATA = b"\x02\xf0\x80"
_USER_ID_BASE = 1001


def _frame(payload: bytes) -> bytes:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= 8:
        raise MCSChannelError("Invalid MCS PDU size")
    return b"\x03\x00" + struct.pack(">H", len(payload) + 7) + _X224_DATA + payload


def _unframe(packet: bytes, size: int, pdu_type: int) -> bytes:
    if (not isinstance(packet, bytes) or len(packet) != size
            or packet[:2] != b"\x03\x00"
            or int.from_bytes(packet[2:4], "big") != size
            or packet[4:7] != _X224_DATA or packet[7] != pdu_type):
        raise MCSChannelError("Unexpected or malformed MCS confirmation")
    return packet[8:]


def _channel(value: int) -> int:
    if type(value) is not int or not _USER_ID_BASE <= value <= 65535:
        raise MCSChannelError("Invalid MCS channel ID")
    return value


def erect_domain_request() -> bytes:
    """subHeight=0 and subInterval=0, Microsoft 4.1.5 wire example."""
    return _frame(b"\x04\x01\x00\x01\x00")


def attach_user_request() -> bytes:
    return _frame(b"\x28")


def parse_attach_user_confirm(packet: bytes) -> int:
    """MCS user ID is PER-encoded as an offset from 1001."""
    body = _unframe(packet, 11, 0x2E)
    if len(body) != 3 or body[0] != 0:
        raise MCSChannelError("MCS attach user rejected or lacks identity")
    return _channel(_USER_ID_BASE + int.from_bytes(body[1:3], "big"))


def channel_join_request(user_id: int, channel_id: int) -> bytes:
    user, channel = _channel(user_id), _channel(channel_id)
    return _frame(b"\x38" + struct.pack(">HH", user - _USER_ID_BASE, channel))


def parse_channel_join_confirm(packet: bytes, *, user_id: int,
                               requested_channel: int) -> None:
    """Requires a successful reply for exactly the most recent request."""
    user_id, requested_channel = _channel(user_id), _channel(requested_channel)
    body = _unframe(packet, 15, 0x3E)
    if len(body) != 7 or body[0] != 0:
        raise MCSChannelError("MCS channel join rejected")
    sender, requested, joined = struct.unpack(">HHH", body[1:7])
    if (sender != user_id - _USER_ID_BASE
            or requested != requested_channel or joined != requested_channel):
        raise MCSChannelError("MCS channel join confirmation mismatched request")


@dataclass(frozen=True)
class MCSJoinedChannels:
    user_channel_id: int
    io_channel_id: int
    joined_channel_ids: tuple[int, int]
    desktop_available: bool = False


def required_channel_ids(user_id: int, io_id: int) -> tuple[int, int]:
    user_id, io_id = _channel(user_id), _channel(io_id)
    if user_id == io_id:
        raise MCSChannelError("User and I/O channels must be distinct")
    return user_id, io_id


__all__ = [
    "MCSChannelError", "MCSJoinedChannels", "erect_domain_request",
    "attach_user_request", "parse_attach_user_confirm", "channel_join_request",
    "parse_channel_join_confirm", "required_channel_ids",
]
