"""Actual binary RDP Bitmap Update -> transactional RGB surface, no server."""
import struct
import pytest

from leshy.rdp_bitmap_update import parse_bitmap_update, RDPBitmapError
from leshy.rdp_bitmap_compositor import RDPBitmapCompositor

SHARE, SERVER, IO = 0x103ea, 1002, 1003


def wire(*, y=0, bgr=b"\0\0\xff\0\xff\0\0\0", flags=0):
    bitmap = struct.pack("<9H", 0, y, 1, y, 2, 1, 24, flags, len(bgr)) + bgr
    update = struct.pack("<HH", 1, 1) + bitmap
    pdu = (struct.pack("<HHH", 18 + len(update), 0x17, SERVER)
           + struct.pack("<IBBHBBH", SHARE, 0, 1, len(update) + 4, 2, 0, 0)
           + update)
    mcs = (b"\x02\xf0\x80\x68" + struct.pack(">HH", SERVER - 1001, IO)
           + b"\x70" + bytes((len(pdu),)) + pdu)
    return b"\x03\x00" + struct.pack(">H", len(mcs) + 4) + mcs


def decode(packet):
    return parse_bitmap_update(packet, share_id=SHARE, server_user_id=SERVER,
                               io_channel_id=IO, desktop_width=2, desktop_height=2)


def test_two_real_protocol_updates_fill_surface_not_before():
    surface = RDPBitmapCompositor(2, 2)
    assert surface.apply(decode(wire(y=0))) is None
    assert surface.covered_pixels == 2
    frame = surface.apply(decode(wire(y=1, bgr=bytes.fromhex("ff0000 ffffff 0000"))))
    assert frame.rgb == bytes.fromhex("ff0000 00ff00 0000ff ffffff")
    assert frame.full_coverage and not frame.input_enabled


def test_duplicate_network_packet_does_not_fake_unknown_row():
    surface = RDPBitmapCompositor(2, 2)
    assert surface.apply(decode(wire())) is None
    assert surface.apply(decode(wire())) is None
    assert surface.covered_pixels == 2


def test_bad_wire_cannot_corrupt_prior_complete_snapshot():
    surface = RDPBitmapCompositor(2, 1)
    full = surface.apply(decode(wire()))
    with pytest.raises(RDPBitmapError):
        surface.apply(decode(wire()[:-1]))
    assert surface.snapshot() == full


def test_compressed_wire_rejected_without_guessing_pixels():
    surface = RDPBitmapCompositor(2, 2)
    with pytest.raises(RDPBitmapError):
        surface.apply(decode(wire(flags=1)))
    assert surface.snapshot() is None
