"""Deterministic standalone wire tests; never contact a Windows RDP host."""
import struct
import pytest

from leshy.rdp_bitmap_update import (
    RDPBitmapError, RDPBitmapUnsupported, parse_bitmap_update,
)

SHARE, SERVER, IO = 0x103ea, 1002, 1003


def rectangle(*, x=0, y=0, w=2, h=2, bpp=24, flags=0, pixels=None):
    if pixels is None:
        stride = (w * (bpp // 8) + 3) & ~3
        pixels = bytes(stride * h)
    return (struct.pack("<9H", x, y, x+w-1, y+h-1, w, h,
                        bpp, flags, len(pixels)) + pixels)


def wire(rectangles=None, *, src=SERVER, destination=IO, share=SHARE,
         sharetype=0x17, subtype=2, plain_size=None, count=None,
         compressed=0, extra=b""):
    rects = [rectangle()] if rectangles is None else rectangles
    payload = struct.pack("<HH", 1, len(rects) if count is None else count) + b"".join(rects) + extra
    pdu = (struct.pack("<HHH", 18+len(payload), sharetype, src)
           + struct.pack("<IBBHBBH", share, 0, 1,
                         len(payload)+4 if plain_size is None else plain_size,
                         subtype, compressed, 0) + payload)
    length = (bytes([len(pdu)]) if len(pdu) < 128
              else bytes([0x80 | (len(pdu)>>8), len(pdu) & 255]))
    mcs = b"\x02\xf0\x80\x68" + struct.pack(">HH", src-1001,destination) + b"\x70" + length + pdu
    return b"\x03\x00" + struct.pack(">H",len(mcs)+4) + mcs


def parse(packet):
    return parse_bitmap_update(packet, share_id=SHARE, server_user_id=SERVER,
                               io_channel_id=IO, desktop_width=64, desktop_height=64)


def test_24_bit_bottom_up_bgr_to_top_down_rgb_and_padding():
    # first storage row: screen bottom = blue, white. second storage row:
    # screen top = red, green. Each 2x24bit line padded with two bytes.
    pixels = bytes.fromhex("ff0000 ffffff aabb 0000ff 00ff00 0102")
    rects = parse(wire([rectangle(w=2,h=2,pixels=pixels)]))
    assert len(rects) == 1
    assert (rects[0].left, rects[0].top, rects[0].width, rects[0].height) == (0,0,2,2)
    assert rects[0].rgb == bytes.fromhex("ff0000 00ff00 0000ff ffffff")
    assert rects[0].pixel_format == "RGB"


def test_rgb565_16_bit_and_row_padding():
    # bottom screen line: blue, white. top screen line: red, green.
    pixels = bytes.fromhex("1f00 ffff 00f8 e007")
    rects = parse(wire([rectangle(w=2,h=2,bpp=16,pixels=pixels)]))
    assert rects[0].rgb == bytes.fromhex("ff0000 00ff00 0000ff ffffff")


def test_bgrx32_ignores_alpha_byte():
    pixels = bytes.fromhex("03020100 302010ff")
    rects = parse(wire([rectangle(w=2,h=1,bpp=32,pixels=pixels)]))
    assert rects[0].rgb == bytes.fromhex("010203 102030")


def test_two_rectangles_and_positions_preserved():
    rects = parse(wire([rectangle(x=2,y=3,w=1,h=1),
                        rectangle(x=10,y=8,w=1,h=1,bpp=32)]))
    assert [(r.left,r.top,r.width,r.height) for r in rects] == [
        (2,3,1,1),(10,8,1,1)
    ]


@pytest.mark.parametrize("change", [
    lambda pkt: pkt[:-1],
    lambda pkt: pkt + b"\0",
    lambda pkt: b"\x04" + pkt[1:],
    lambda pkt: pkt[:7] + b"\x64" + pkt[8:],
    lambda pkt: pkt[:10] + b"\x03\xec" + pkt[12:],
    lambda pkt: pkt[:14] + b"\x00\x00" + pkt[16:],
])
def test_invalid_tpkt_or_mcs_rejected(change):
    with pytest.raises(RDPBitmapError):
        parse(change(wire()))


@pytest.mark.parametrize("kwargs", [
    {"src":1004}, {"destination":1004}, {"share":123},
    {"sharetype":0x13}, {"subtype":0x1f},
    {"plain_size":0}, {"compressed":0x20},
    {"extra":b"\x00"}, {"count":0}, {"count":65},
])
def test_invalid_header_or_count_rejected(kwargs):
    with pytest.raises(RDPBitmapError):
        parse(wire(**kwargs))


@pytest.mark.parametrize("bpp,flags,w,h", [
    (8,0,2,2), (15,0,2,2), (16,1,2,2),
    (24,0x401,2,2), (32,0x400,2,2),
    (24,0,65,2), (24,0,2,65),
])
def test_compressed_unsupported_or_out_of_desktop_rejected(bpp,flags,w,h):
    with pytest.raises(RDPBitmapError):
        parse(wire([rectangle(bpp=bpp,flags=flags,w=w,h=h)]))


def test_width_clipping_is_explicitly_not_implemented():
    r = bytearray(rectangle(w=2,h=2))
    struct.pack_into("<H",r,4,4)  # destRight=4 but bitmap width=2
    with pytest.raises(RDPBitmapUnsupported, match="clipping"):
        parse(wire([bytes(r)]))


def test_partial_rectangle_not_a_complete_frame():
    (r,) = parse(wire([rectangle(x=1,y=1,w=1,h=1)]))
    assert r.width == r.height == 1
    assert len(r.rgb) == 3  # not an invented full desktop


def test_no_trailing_bytes_after_rectangles():
    with pytest.raises(RDPBitmapError, match="Trailing"):
        parse(wire([rectangle()],extra=b"\x00\x00"))
