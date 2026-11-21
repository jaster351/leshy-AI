"""RDP6 planar streams inside TPKT/MCS slow-path Bitmap Updates."""
import struct
import pytest
from leshy.rdp_bitmap_update import RDPBitmapError, RDPBitmapUnsupported, parse_bitmap_update

SID, SERVER, IO = 0x103ea, 1002, 1003


def packet(stream: bytes, *, width=2, height=2, flags=0x0401, header=False, depth=32):
    if header:
        cd_header = struct.pack("<4H", 0, len(stream), width * (depth // 8),
                                width * height * (depth // 8))
        stream = cd_header + stream
    bitmap = struct.pack("<9H", 0, 0, width - 1, height - 1,
                         width, height, depth, flags, len(stream)) + stream
    update = struct.pack("<HH", 1, 1) + bitmap
    share = (struct.pack("<HHH", 18 + len(update), 0x0017, SERVER)
             + struct.pack("<IBBHBBH", SID, 0, 1, len(update) + 4, 2, 0, 0) + update)
    per = bytes([len(share)]) if len(share) < 128 else bytes([
        0x80 | (len(share) >> 8), len(share) & 255
    ])
    mcs = b"\x02\xf0\x80\x68" + struct.pack(">HH", SERVER - 1001, IO) + b"\x70" + per + share
    return b"\x03\x00" + struct.pack(">H", len(mcs) + 4) + mcs


def decode(stream: bytes, **kwargs):
    return parse_bitmap_update(
        packet(stream, **kwargs), share_id=SID, server_user_id=SERVER,
        io_channel_id=IO, desktop_width=64, desktop_height=64,
    )


RAW = bytes([0x20]) + bytes([255, 0, 0, 255]) + bytes([0, 255, 0, 255]) + bytes([0, 0, 255, 255])
FRAME = bytes.fromhex("0000ff ffffff ff0000 00ff00")


@pytest.mark.parametrize("flags,with_header", [(0x0401, False), (1, True)])
def test_rdp6_raw_noalpha_with_and_without_compression_header(flags, with_header):
    (r,) = decode(RAW, flags=flags, header=with_header)
    assert r.rgb == FRAME
    assert (r.width, r.height, r.pixel_format) == (2, 2, "RGB")


def test_rdp6_alpha_opaque_and_planar_rle():
    rgba = b"\0" + b"\xff" * 4 + RAW[1:]
    assert decode(rgba)[0].rgb == FRAME
    stream = b"\x30" + (b"\x13\xff") + (b"\x13\x00") + (b"\x13\x00")
    assert decode(stream, width=4, height=1)[0].rgb == bytes.fromhex("ff0000") * 4


@pytest.mark.parametrize("stream", [
    b"\x28" + RAW[1:],  # chroma subsampling
    b"\x30\0",          # truncated RLE
    b"\x20\0",          # truncated RGB planes
    b"\x40" + RAW[1:],  # reserved format bit
    b"\x20" + RAW[1:] + b"\0\0",  # invalid two-byte padding
])
def test_unsupported_and_corrupt_modes_fail_closed(stream):
    with pytest.raises(RDPBitmapError):
        decode(stream)


def test_nonopaque_alpha_and_bad_compression_header_rejected():
    alpha = b"\0" + bytes([255, 0, 255, 255]) + RAW[1:]
    with pytest.raises(RDPBitmapUnsupported):
        decode(alpha)
    with pytest.raises(RDPBitmapError):
        decode(RAW, flags=1, header=False)


def test_truncated_tpkt_rejected():
    with pytest.raises(RDPBitmapError):
        parse_bitmap_update(
            packet(RAW)[:-1], share_id=SID, server_user_id=SERVER,
            io_channel_id=IO, desktop_width=64, desktop_height=64,
        )


# AYCoCg is now supported, including half-resolution chroma with odd bounds.
def test_ycocg_32bit_single_pixel_and_cd_header():
    stream = bytes([0x21, 100, 10, 20])  # CLL=1, no alpha, Y/Co/Cg
    assert decode(stream, width=1, height=1)[0].rgb == bytes([70,120,90])
    assert decode(stream, width=1, height=1, flags=1, header=True)[0].rgb == bytes([70,120,90])


def test_ycocg_subsampled_odd_size_in_real_mcs_frame():
    stream = bytes([0x29]) + bytes([100]*9) + bytes([10,20,30,40]) + bytes([0]*4)
    patches=decode(stream, width=3, height=3)
    expected = b''.join(bytes([100-c,100,100+c]) for c in [30,30,40,10,10,20,10,10,20])
    assert len(patches)==1 and patches[0].rgb==expected


def test_ycocg_subsampled_rle_odd_size_in_real_mcs_frame():
    yy=b'\x30\x64\x64\x64'+b'\x30\x00\x00\x00'*2
    co=b'\x20\x0a\x0a'+b'\x20\x00\x00'
    cg=b'\x20\x00\x00'*2
    stream=bytes([0x39])+yy+co+cg
    assert decode(stream,width=3,height=3)[0].rgb == bytes([90,100,110])*9


def test_reject_invalid_ycocg_subsampling_wire():
    invalid = [bytes([0x28])+bytes([0]*12),  # chroma requires nonzero CLL
               bytes([0x29])+bytes([100]*9)+bytes([10]*4)+bytes([0]*3),
               bytes([0x29])+bytes([100]*9)+bytes([10]*4)+bytes([0]*4)+b'\0\0',
               bytes([0x39])+b'\x10']
    for stream in invalid:
        with pytest.raises(RDPBitmapError):
            decode(stream,width=3,height=3)
