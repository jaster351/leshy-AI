"""No external RDP host: deterministic valid-client and Demand Active PDUs."""
from __future__ import annotations
import struct
import pytest
from leshy.rdp_server_activation import (RDPActivationError, read_server_mcs_data,
                                         parse_valid_client_license, parse_demand_active)


def sd_in(body, io=1003, origin=1002):
    per = bytes((len(body),)) if len(body) < 128 else bytes((0x80 | (len(body) >> 8), len(body) & 255))
    payload = b"\x02\xf0\x80\x68" + struct.pack(">HH", origin - 1001, io) + b"\x70" + per + body
    return b"\x03\x00" + struct.pack(">H", 4 + len(payload)) + payload


def license_valid():
    return bytes.fromhex("80000000ff031000070000000200000004000000")


def demand():
    caps = b"\x01\x00\x04\x00" + b"\x02\x00\x04\x00"
    body = struct.pack("<IHH", 0x000103ea, 4, 4+len(caps)) + b"RDP\x00" + struct.pack("<HH", 2, 0) + caps + struct.pack("<I", 0)
    return struct.pack("<HHH", 6 + len(body), 0x0011, 1002) + body


def test_valid_client_and_demand_active():
    source, pkt = read_server_mcs_data(sd_in(license_valid()), expected_io_channel=1003)
    assert source == 1002
    parse_valid_client_license(pkt)
    source, pkt = read_server_mcs_data(sd_in(demand()), expected_io_channel=1003)
    assert source == 1002
    assert parse_demand_active(pkt) == (0x000103ea, (1, 2))

@pytest.mark.parametrize('bad', [
    lambda p:p[:2]+b'\x00\x10'+p[4:],
    lambda p:p[:7]+b'\x64'+p[8:],
    lambda p:p[:10]+b'\x03\xec'+p[12:],
    lambda p:p[:12]+b'\x60'+p[13:],
    lambda p:p[:-1],
    lambda p:p+b'\x00',
    lambda p:p[:13]+b'\xff'+p[14:],
    lambda p:p[:13]+b'\x80\x00'+p[14:],
])
def test_invalid_mcs_frame_rejected(bad):
    with pytest.raises(RDPActivationError):
        read_server_mcs_data(bad(sd_in(license_valid())), expected_io_channel=1003)

@pytest.mark.parametrize('bad', [
    lambda p:p[:4]+b'\x00'+p[5:],
    lambda p:p[:4]+b'\x08'+p[5:],
    lambda p:p[:8]+b'\x01'+p[9:],
    lambda p:p[:11]+b'\x01'+p[12:],
    lambda p:p[:-1],
])
def test_invalid_license_denied(bad):
    with pytest.raises(RDPActivationError):
        parse_valid_client_license(bad(license_valid()))

@pytest.mark.parametrize('bad', [
    lambda p:p[:2]+b'\x00\x00'+p[4:],
    lambda p:p[:2]+b'\x17\x00'+p[4:],
    lambda p:p[:10]+b'\xff\xff'+p[12:],
    lambda p:p[:14]+b'RDPA'+p[18:],
    lambda p:p[:18]+b'\xff\x00'+p[20:],
    lambda p:p[:22]+b'\x02\x00'+p[24:],
    lambda p:p[:-2],
    lambda p:p+b'\x00',
])
def test_invalid_demand_active_denied(bad):
    with pytest.raises(RDPActivationError):
        parse_demand_active(bad(demand()))


def test_unknown_license_flow_is_explicitly_not_supported():
    with pytest.raises(RDPActivationError, match='Unsupported'):
        parse_valid_client_license(b'\x80\x00\x00\x00' + b'\x12' * 30)


def test_ignored_license_flags_hi_per_spec():
    data = license_valid()
    parse_valid_client_license(data[:2] + b"\x11\x22" + data[4:])
