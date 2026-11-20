"""Additional boundary regressions for credential-free RDP Client Info.

These tests do not establish a successful Windows logon or desktop session.
"""
from __future__ import annotations

import struct

import pytest

from leshy.rdp_client_info import RDPClientInfoError, build_client_info_pdu


@pytest.mark.parametrize("user,io", [
    (1001, 1002),
    (65535, 65534),
    (1002, 65535),
])
def test_extreme_valid_mcs_channel_ids_preserve_wire_values(user, io):
    packet = build_client_info_pdu(user, io, client_address="192.0.2.1")
    assert len(packet) == struct.unpack(">H", packet[2:4])[0]
    assert struct.unpack(">HH", packet[8:12]) == (user - 1001, io)
    assert packet[12] == 0x70


@pytest.mark.parametrize("user,io", [
    (1000, 1002),
    (1001, 1001),
    (65536, 1002),
    (1001, 65536),
    (1001.0, 1002),
    (1001, False),
    (None, 1002),
])
def test_channel_boundaries_fail_closed(user, io):
    with pytest.raises(RDPClientInfoError):
        build_client_info_pdu(user, io, client_address="192.0.2.1")


@pytest.mark.parametrize("address", [
    "::", "ff02::1", "ff0e::1", "0.0.0.0", "239.1.2.3",
    "fe80::1%3", "192.0.2.1:3389", "[2001:db8::1]",
    "example.com", " 192.0.2.1", "192.0.2.1 ",
])
def test_unusable_or_nonliteral_address_rejected(address):
    with pytest.raises(RDPClientInfoError):
        build_client_info_pdu(1007, 1003, client_address=address)


def test_ipv6_client_info_has_no_password_and_uses_literal_address():
    packet = build_client_info_pdu(1007, 1003, client_address="2001:0db8:0:0::1")
    # Fixed TS_INFO_PACKET offsets: all five credential/shell lengths are zero.
    body = packet[14:] if packet[13] < 128 else packet[15:]
    assert body[:4] == bytes((0x40, 0, 0, 0))
    assert struct.unpack_from("<5H", body, 12) == (0, 0, 0, 0, 0)
    assert body[22:32] == bytes(10)
    assert "2001:db8::1".encode("utf-16-le") in body
