"""RDP Confirm Active wire fields and negative capability negotiation tests."""
import struct
import pytest
from leshy.rdp_confirm_active import build_confirm_active, RDPConfirmActiveError


def unpack(packet):
    assert packet[:2] == b"\x03\x00"
    assert int.from_bytes(packet[2:4], "big") == len(packet)
    assert packet[4:8] == b"\x02\xf0\x80\x64"
    src, dest = struct.unpack_from(">HH", packet, 8)
    assert packet[12] == 0x70
    length_first = packet[13]
    if length_first & 0x80:
        n = ((length_first & 0x3f) << 8) | packet[14]
        at = 15
    else:
        n, at = length_first, 14
    assert len(packet) - at == n
    pdu = packet[at:]
    total, kind, source = struct.unpack_from("<HHH", pdu)
    assert (total, kind, source) == (len(pdu), 0x13, src + 1001)
    share, origin, dlen, clen = struct.unpack_from("<IHHH", pdu, 6)
    assert (share, origin) == (0x103ea, 1002)
    assert pdu[16:16+dlen] == b"LESHY\x00"
    capabilities = pdu[16+dlen:]
    assert len(capabilities) == clen
    count, padding = struct.unpack_from("<HH", capabilities)
    assert count == 7 and padding == 0
    pos = 4
    blocks = {}
    for _ in range(count):
        kind, size = struct.unpack_from("<HH", capabilities, pos)
        assert kind not in blocks and size >= 4
        blocks[kind] = capabilities[pos+4:pos+size]
        pos += size
    assert pos == len(capabilities)
    return blocks, src, dest


@pytest.mark.parametrize("depth", [16, 24, 32])
def test_confirm_active_wire(depth):
    packet = build_confirm_active(
        share_id=0x103ea, user_channel_id=1007, io_channel_id=1003,
        width=1280, height=720, color_depth=depth,
        server_capability_types=(1, 2, 9),
    )
    caps, src, dest = unpack(packet)
    assert (src, dest) == (6, 1003)
    assert list(caps) == [1, 2, 5, 7, 8, 9, 13]
    assert len(caps[1]) == 20 and len(caps[2]) == 24
    assert struct.unpack_from("<H", caps[2])[0] == depth
    assert struct.unpack_from("<H", caps[2], 16)[0] == 1
    assert struct.unpack_from("<H", caps[2], 20)[0] == 1
    assert caps[13][:2] == b"\x01\x00"


@pytest.mark.parametrize("params", [
    {"share_id": 0}, {"share_id": 2**32}, {"share_id": True},
    {"user_channel_id": 1000}, {"user_channel_id": 65536},
    {"user_channel_id": True}, {"io_channel_id": 1000},
    {"io_channel_id": 65536}, {"io_channel_id": True},
    {"user_channel_id": 1003}, {"width": 0}, {"width": 8193},
    {"width": True}, {"height": 199}, {"height": 8193},
    {"height": True}, {"color_depth": 8}, {"color_depth": True},
    {"server_capability_types": (9,)},
    {"server_capability_types": [1, 2]},
])
def test_reject_bad_params(params):
    kwargs = dict(share_id=0x103ea, user_channel_id=1007,
                  io_channel_id=1003, server_capability_types=(1, 2))
    kwargs.update(params)
    with pytest.raises(RDPConfirmActiveError):
        build_confirm_active(**kwargs)


def test_no_legacy_header_no_virtual_channels():
    packet = build_confirm_active(
        share_id=0x103ea, user_channel_id=1007, io_channel_id=1003,
        server_capability_types=(1, 2),
    )
    caps, _, _ = unpack(packet)
    assert not set(caps) & {15, 17, 18, 19, 20, 21, 23, 26, 28, 29, 30}
    assert b"LESHY\x00" in packet
