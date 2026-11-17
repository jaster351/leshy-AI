"""Credential-free RDP Client Info PDU over an already pinned TLS/MCS channel.

MS-RDPBCGR 2.2.1.11: MCS SendDataRequest, mandatory SEC_INFO_PKT header,
TS_INFO_PACKET and minimal TS_EXTENDED_INFO_PACKET. This builder MUST only
be called after owner-approved CredSSP handoff and both MCS Channel Joins.

There is deliberately NO password/auto-logon/shell parameter, and the packet
never claims that Windows logon or a usable framebuffer has completed.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import struct

_MAX_PDU = 8192
_MCS_USER_BASE = 1001
_X224_DATA = b"\x02\xf0\x80"
_SEC_INFO_PKT = 0x0040
_INFO_UNICODE = 0x00000010
_INFO_LOGONNOTIFY = 0x00000040
_INFO_NOAUDIOPLAYBACK = 0x00080000
_CLIENT_INFO_FLAGS = _INFO_UNICODE | _INFO_LOGONNOTIFY | _INFO_NOAUDIOPLAYBACK


class RDPClientInfoError(ValueError):
    """Invalid RDP Client Info packet parameters (no sensitive payload)."""


@dataclass(frozen=True)
class RDPClientInfoReceipt:
    """An outbound protocol PDU was submitted, NOT a successful Windows logon."""
    user_channel_id: int
    io_channel_id: int
    sent: bool = True
    windows_logon_verified: bool = False
    desktop_available: bool = False


def _channel_id(value: int) -> int:
    if type(value) is not int or not _MCS_USER_BASE <= value <= 65535:
        raise RDPClientInfoError("Invalid MCS channel ID")
    return value


def _per_length(size: int) -> bytes:
    if type(size) is not int or not 0 < size <= 0x3FFF:
        raise RDPClientInfoError("Client Info exceeds MCS PER bound")
    if size < 128:
        return bytes((size,))
    return bytes((0x80 | (size >> 8), size & 0xFF))


def _unicode(value: str, *, max_bytes: int) -> bytes:
    if (not isinstance(value, str) or not value
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise RDPClientInfoError("Invalid RDP Client Info string")
    try:
        encoded = value.encode("utf-16-le", "strict") + b"\x00\x00"
    except UnicodeError as exc:
        raise RDPClientInfoError("Invalid Unicode string") from exc
    if len(encoded) > max_bytes:
        raise RDPClientInfoError("Client Info string exceeds limit")
    return encoded


def build_client_info_pdu(
    user_channel_id: int,
    io_channel_id: int,
    *,
    client_address: str,
) -> bytes:
    """Build a minimal, non-autologon Client Info PDU for enhanced TLS RDP.

    client_address must be the client's literal IP, not a URL or remote SPN.
    Password/username/domain are always empty; they cannot be injected here.
    The real RDP server receives the username separately via approved CredSSP.
    The client directory is an intentionally generic path, not a local secret.
    """
    user = _channel_id(user_channel_id)
    io = _channel_id(io_channel_id)
    if user == io:
        raise RDPClientInfoError("User and I/O MCS channels must be distinct")
    try:
        address = ipaddress.ip_address(client_address)
    except (ValueError, TypeError) as exc:
        raise RDPClientInfoError("Client address must be a literal IP") from exc
    if address.version == 4:
        family = 2
    else:
        family = 0x17
    address_bytes = _unicode(str(address), max_bytes=80)
    # Do not reveal the actual workstation path; the protocol expects a
    # client directory but does not need a real path for non-RemoteApp RDP.
    directory_bytes = _unicode("C:\\", max_bytes=512)

    # five mandatory null-terminated strings, all empty by construction:
    # domain, username, password, alternateShell and workingDir.
    strings = b"\x00\x00" * 5
    info_header = struct.pack("<IIHHHHH", 0x00000409, _CLIENT_INFO_FLAGS,
                              0, 0, 0, 0, 0)
    extended = (struct.pack("<HH", family, len(address_bytes)) + address_bytes
                + struct.pack("<H", len(directory_bytes)) + directory_bytes)
    # Enhanced RDP security: basic 4-byte header, not legacy SEC_ENCRYPT.
    security_header = struct.pack("<HH", _SEC_INFO_PKT, 0)
    user_data = security_header + info_header + strings + extended
    # MCS SendDataRequest, high priority, segmentation begin|end.
    # Initiator is PER-encoded as the user ID minus 1001.
    mcs = (b"\x64" + struct.pack(">HH", user - _MCS_USER_BASE, io)
           + b"\x70" + _per_length(len(user_data)) + user_data)
    tpkt_length = 7 + len(mcs)
    if tpkt_length > _MAX_PDU:
        raise RDPClientInfoError("Client Info packet exceeds configured limit")
    return b"\x03\x00" + struct.pack(">H", tpkt_length) + _X224_DATA + mcs


__all__ = ["RDPClientInfoError", "RDPClientInfoReceipt", "build_client_info_pdu"]
