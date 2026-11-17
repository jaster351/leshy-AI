"""MS-RDPBCGR Client Info packet layout, no credentials or auto logon."""
from __future__ import annotations

import ipaddress
import struct

import pytest

from leshy.rdp_client_info import (
    RDPClientInfoError, build_client_info_pdu,
)


def unpack(packet):
    assert packet[:2] == b"\x03\x00"
    assert int.from_bytes(packet[2:4], "big") == len(packet)
    assert packet[4:8] == b"\x02\xf0\x80\x64"
    sender_offset, io = struct.unpack(">HH", packet[8:12])
    assert packet[12] == 0x70
    pos = 13
    length = packet[pos]
    pos += 1
    if length >= 128:
        length = ((length & 0x3f) << 8) | packet[pos]
        pos += 1
    body = packet[pos:]
    assert len(body) == length
    return sender_offset + 1001, io, body


@pytest.mark.parametrize("address,family", [
    ("127.0.0.1", 2),
    ("192.0.2.15", 2),
    ("2001:db8::25", 0x17),
])
def test_client_info_pdu_over_enhanced_tls(address, family):
    packet = build_client_info_pdu(1007, 1003, client_address=address)
    user, io, body = unpack(packet)
    assert (user, io) == (1007, 1003)
    assert body[:4] == b"\x40\x00\x00\x00"  # SEC_INFO_PKT, no SEC_ENCRYPT
    lang, flags, *lengths = struct.unpack_from("<IIHHHHH", body, 4)
    assert lang == 0x409 and flags == 0x80050
    assert not flags & 0x08  # INFO_AUTOLOGON
    assert lengths == [0, 0, 0, 0, 0]
    assert body[22:32] == b"\x00" * 10  # five empty, terminated strings
    af, cb_addr = struct.unpack_from("<HH", body, 32)
    assert af == family
    expected = (str(ipaddress.ip_address(address)) + "\0").encode("utf-16-le")
    assert cb_addr == len(expected)
    assert body[36:36 + cb_addr] == expected
    offset = 36 + cb_addr
    cb_dir = struct.unpack_from("<H", body, offset)[0]
    assert body[offset + 2:] == "C:\\\0".encode("utf-16-le")
    assert cb_dir == len(body[offset + 2:])
    assert len(packet) < 512


def test_credentials_cannot_be_added_to_builder():
    with pytest.raises(TypeError):
        build_client_info_pdu(1007, 1003, client_address="192.0.2.1",
                              password="TOP-SECRET")
    packet = build_client_info_pdu(1007, 1003, client_address="192.0.2.1")
    assert b"TOP-SECRET" not in packet
    assert not unpack(packet)[2][8] & 8


@pytest.mark.parametrize("user, io, address", [
    (0, 1003, "192.0.2.1"),
    (1007, 1007, "192.0.2.1"),
    (1007, 1000, "192.0.2.1"),
    (65536, 1003, "192.0.2.1"),
    (True, 1003, "192.0.2.1"),
    (1007, 1003, ""),
    (1007, 1003, "example.org"),
    (1007, 1003, "fe80::1%eth0"),
    (1007, 1003, "0.0.0.0"),
    (1007, 1003, "224.0.0.1"),
    (1007, 1003, 0xC0000201),
])
def test_invalid_input_rejected(user, io, address):
    with pytest.raises(RDPClientInfoError):
        build_client_info_pdu(user, io, client_address=address)
