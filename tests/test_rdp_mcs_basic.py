"""RDP MCS/GCC BER/PER wire-level framing and rejection tests."""
from __future__ import annotations
import struct
import pytest
from leshy import rdp_mcs_basic as m


def response(*, method=0, level=0, channel_count=0, gcc_key=b"McDn", result=0,
             requested_protocols=0x0B):
    core = struct.pack("<HHII", 0x0C01, 12, 0x80004, requested_protocols)
    channels = struct.pack("<HH", 1003, channel_count)
    channels += b"".join(struct.pack("<H", 1004 + i) for i in range(channel_count))
    if channel_count % 2:
        channels += b"\0\0"
    net = struct.pack("<HH", 0x0C03, len(channels) + 4) + channels
    sec = struct.pack("<HHII", 0x0C02, 12, method, level)
    blocks = core + net + sec
    conference = b"\x14" + bytes.fromhex("760a01010001c000") + gcc_key
    conference += m._per_length(len(blocks)) + blocks
    gcc = m._GCC_OID + m._per_length(len(conference)) + conference
    body = b"\x0a\x01" + bytes([result]) + b"\x02\x01\x00"
    body += m._domain((34, 3, 0, 1, 0, 1, 65528, 2)) + m._ber(b"\x04", gcc)
    packet = b"\x02\xf0\x80" + m._ber(b"\x7f\x66", body)
    return b"\x03\x00" + struct.pack(">H", 4 + len(packet)) + packet


def test_build_mcs_has_consistent_lengths_and_no_redirection():
    pkt = m.build_mcs_connect_initial(1280, 720, selected_protocol=2)
    assert pkt[:7] == b"\x03\x00\x01\x70\x02\xf0\x80"
    assert len(pkt) == 368
    assert pkt[7:12] == bytes.fromhex("7f65820164")
    assert pkt.count(b"Duca") == 1
    assert bytes.fromhex("01c0d800") in pkt  # TS_UD_CS_CORE: 216 bytes
    assert bytes.fromhex("02c00c000000000000000000") in pkt  # external TLS only
    assert pkt.endswith(bytes.fromhex("03c0080000000000"))  # 0 static channels
    assert bytes.fromhex("4c0045005300480059002d0041004900") in pkt


def test_mcs_echoes_selected_credssp_protocol():
    p2 = m.build_mcs_connect_initial(selected_protocol=2)
    p8 = m.build_mcs_connect_initial(selected_protocol=8)
    assert p2 != p8 and len(p2) == len(p8)
    assert p2[-12:] == p8[-12:]


def test_parse_enhanced_security_server_response():
    result = m.parse_mcs_connect_response(response())
    assert result.io_channel_id == 1003
    assert result.static_channels == ()
    assert result.core_version == 0x80004
    assert result.server_requested_protocols == 0x0B


@pytest.mark.parametrize("changes", [
    {"method": 2}, {"level": 2}, {"gcc_key": b"Duca"},
    {"result": 1}, {"channel_count": 3}, {"channel_count": 32},
    {"requested_protocols": 0x02},
])
def test_unsafe_server_response_rejected(changes):
    with pytest.raises(m.MCSError):
        m.parse_mcs_connect_response(response(**changes))


@pytest.mark.parametrize("mutate", [
    lambda p: p[:-1],
    lambda p: p + b"\0",
    lambda p: b"\x04" + p[1:],
    lambda p: p[:4] + b"\x03" + p[5:],
    lambda p: p[:7] + b"\x7f\x65" + p[9:],
])
def test_malformed_tpkt_mcs_frames_rejected(mutate):
    with pytest.raises(m.MCSError):
        m.parse_mcs_connect_response(mutate(response()))


@pytest.mark.parametrize("w,h,proto,name", [
    (0,720,2,"LESHY"), (10000,720,2,"LESHY"), (1280,720,0,"LESHY"),
    (1280,720,1,"LESHY"), (1280,720,2,"A"*16), (1280,720,2,"\n"),
])
def test_invalid_client_settings_rejected(w,h,proto,name):
    with pytest.raises(m.MCSError):
        m.build_mcs_connect_initial(w, h, selected_protocol=proto, client_name=name)
