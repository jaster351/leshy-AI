"""Bounded, read-only RDP licensing and Demand Active parsing after Client Info.

Enhanced (TLS) RDP only. No licensing requests, password delivery, display,
or keyboard/mouse I/O. All inputs are untrusted server bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

MAX_SERVER_PACKET = 16384
_SEC_LICENSE_PKT = 0x0080
_VALID_CLIENT = bytes.fromhex("ff031000070000000200000004000000")


class RDPActivationError(ValueError):
    """The server replied with unsupported or malformed activation data."""


@dataclass(frozen=True)
class RDPServerActivation:
    share_id: int
    server_mcs_user_id: int
    io_channel_id: int
    capability_types: tuple[int, ...]
    license_valid_client: bool = True
    confirmed_active: bool = False
    desktop_available: bool = False


def read_server_mcs_data(packet: bytes, *, expected_io_channel: int) -> tuple[int, bytes]:
    """Decode one complete TPKT / X224 / MCS SendDataIndication.

    This is not a stream reader; the caller must first read exactly the TPKT
    length under a global deadline and an owner-verified, pinned TLS socket.
    """
    if (type(expected_io_channel) is not int or
            not 1001 <= expected_io_channel <= 65535):
        raise RDPActivationError("Invalid destination I/O channel")
    if (not isinstance(packet, bytes) or not 16 <= len(packet) <= MAX_SERVER_PACKET
            or packet[:2] != b"\x03\x00"
            or int.from_bytes(packet[2:4], "big") != len(packet)
            or packet[4:8] != b"\x02\xf0\x80\x68"):
        raise RDPActivationError("Invalid server MCS TPKT/X224 envelope")
    initiator_offset, destination = struct.unpack_from(">HH", packet, 8)
    if (destination != expected_io_channel or
            not 1001 <= initiator_offset + 1001 <= 65535):
        raise RDPActivationError("Unexpected MCS sender or destination")
    if packet[12] != 0x70:
        raise RDPActivationError("Unsupported MCS priority/fragmentation")
    length_start = packet[13]
    if length_start < 128:
        declared, start = length_start, 14
    else:
        if length_start & 0x40 or len(packet) < 15:
            raise RDPActivationError("Invalid MCS PER length")
        declared = ((length_start & 0x3f) << 8) | packet[14]
        start = 15
        if declared < 128:
            raise RDPActivationError("Noncanonical MCS PER length")
    if declared < 4 or declared != len(packet) - start:
        raise RDPActivationError("Invalid MCS userData bounds")
    return initiator_offset + 1001, packet[start:]


def parse_valid_client_license(data: bytes) -> None:
    """Accept only unencrypted SSL-protected STATUS_VALID_CLIENT Error Alert.

    This is one narrow terminal-server flow. Other valid licensing handshakes
    require a separate RDPELE implementation; they cannot be silently skipped.
    """
    if not isinstance(data, bytes) or len(data) != 20:
        raise RDPActivationError("Unsupported licensing response")
    flags, _flags_hi = struct.unpack_from("<HH", data)
    if flags not in (_SEC_LICENSE_PKT, _SEC_LICENSE_PKT | 0x0200):
        raise RDPActivationError("Licensing must not use legacy RDP encryption")
    # SEC_FLAGSHI_VALID is unset: MS-RDPBCGR requires ignoring flagsHi.
    if data[4:] != _VALID_CLIENT:
        raise RDPActivationError("RDP server did not signal valid client licensing")


def parse_demand_active(data: bytes) -> tuple[int, tuple[int, ...]]:
    """Check complete TS_DEMAND_ACTIVE_PDU structure; no capability enablement.

    Validate boundaries and mandatory General/Bitmap capabilities, returning
    IDs only.  Caller MUST NOT treat this as a logged-in desktop.
    """
    if not isinstance(data, bytes) or not 26 <= len(data) <= MAX_SERVER_PACKET:
        raise RDPActivationError("Malformed RDP Demand Active data")
    total, pdu_type, _source = struct.unpack_from("<HHH", data)
    if total != len(data) or pdu_type != 0x0011:
        raise RDPActivationError("Invalid Demand Active share-control header")
    share_id, desc_len, caps_len = struct.unpack_from("<IHH", data, 6)
    if not share_id or not 1 <= desc_len <= 256 or caps_len < 4:
        raise RDPActivationError("Invalid Demand Active metadata")
    desc_start = 14
    caps_start = desc_start + desc_len
    caps_end = caps_start + caps_len
    if caps_end + 4 != len(data):
        raise RDPActivationError("Demand Active lengths do not match packet")
    descriptor = data[desc_start:caps_start]
    if not descriptor or descriptor[-1] != 0:
        raise RDPActivationError("Demand Active source descriptor invalid")
    count, _pad = struct.unpack_from("<HH", data, caps_start)
    if count < 2 or count > 64:
        raise RDPActivationError("Demand Active capability count invalid")
    types: list[int] = []
    at = caps_start + 4
    for _ in range(count):
        if at + 4 > caps_end:
            raise RDPActivationError("Truncated Demand Active capability")
        kind, size = struct.unpack_from("<HH", data, at)
        if not kind or size < 4 or at + size > caps_end or kind in types:
            raise RDPActivationError("Invalid or duplicate Demand Active capability")
        types.append(kind)
        at += size
    if at != caps_end or not {1, 2}.issubset(types):
        raise RDPActivationError("Missing required RDP General/Bitmap capabilities")
    return share_id, tuple(types)


__all__ = ["MAX_SERVER_PACKET", "RDPActivationError", "RDPServerActivation", "read_server_mcs_data", "parse_valid_client_license", "parse_demand_active"]
