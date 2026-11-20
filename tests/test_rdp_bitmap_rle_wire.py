"""Exact wire-level MS-RDPBCGR compressed Bitmap Update interoperability cases."""
import struct
import pytest
from leshy.rdp_bitmap_update import parse_bitmap_update, RDPBitmapError

SHARE, SERVER, IO = 0x103EA, 1002, 1003
RED = bytes.fromhex('ff0000')


def rectangle(*, width=2, height=2, bpp=24, x=0, y=0, rle=bytes.fromhex('62 00 00 ff 02'), flags=0x0401,
              header=None):
    if header is not None:
        rle = header + rle
    return struct.pack('<9H', x,y,x+width-1,y+height-1,width,height,bpp,flags,len(rle))+rle


def wire(rectangles, *, source=SERVER, dest=IO):
    data = struct.pack('<HH',1,len(rectangles)) + b''.join(rectangles)
    sharedata = (struct.pack('<HHH',18+len(data),0x17,source)
                 + struct.pack('<IBBHBBH',SHARE,0,1,len(data)+4,2,0,0)+data)
    if len(sharedata)<128:
        length=bytes([len(sharedata)])
    else:
        length=bytes([0x80 | (len(sharedata)>>8),len(sharedata)&255])
    mcs = b'\x02\xf0\x80\x68' + struct.pack('>HH',source-1001,dest) + b'\x70'+length+sharedata
    return b'\x03\x00'+struct.pack('>H',len(mcs)+4)+mcs


def decode(packet):
    return parse_bitmap_update(packet,share_id=SHARE,server_user_id=SERVER,io_channel_id=IO,desktop_width=64,desktop_height=64)


def test_no_compression_header_24bpp_and_rle_previous_line():
    r=decode(wire([rectangle()]))
    assert len(r)==1
    assert (r[0].width,r[0].height,r[0].pixel_format)==(2,2,'RGB')
    assert r[0].rgb==RED*4


def test_standard_header_24bpp():
    stream=bytes.fromhex('62 00 00 ff 02')
    hdr=struct.pack('<4H',0,len(stream),8,16)
    r=decode(wire([rectangle(flags=0x1,rle=stream,header=hdr)]))
    assert r[0].rgb==RED*4


def test_standard_header_accepts_tight_uncompressed_size():
    stream=bytes.fromhex('62 00 00 ff 02')
    hdr=struct.pack('<4H',0,len(stream),8,12)
    assert decode(wire([rectangle(flags=0x1,rle=stream,header=hdr)]))[0].rgb==RED*4


def test_16_bit_rgb565_headerless_and_compressed_header():
    stream=bytes.fromhex('62 00 f8 02')
    hdr=struct.pack('<4H',0,len(stream),4,8)
    assert decode(wire([rectangle(bpp=16,rle=stream,flags=0x401)]))[0].rgb==RED*4
    assert decode(wire([rectangle(bpp=16,rle=stream,flags=0x1,header=hdr)]))[0].rgb==RED*4


def test_two_compressed_rectangles_are_independent():
    r=decode(wire([rectangle(x=0),rectangle(x=4)]))
    assert [(v.left,v.width) for v in r]==[(0,2),(4,2)]
    assert all(v.rgb==RED*4 for v in r)


@pytest.mark.parametrize('header',[
    struct.pack('<4H',1,5,8,16),
    struct.pack('<4H',0,6,8,16),
    struct.pack('<4H',0,5,0,16),
    struct.pack('<4H',0,5,6,16),
    struct.pack('<4H',0,5,8,123),
])
def test_reject_invalid_compressed_header(header):
    with pytest.raises(RDPBitmapError):
        decode(wire([rectangle(flags=1,header=header)]))


@pytest.mark.parametrize('flags,bpp',[(1,32),(0x401,32),(0x400,24),(2,24),(0x401,8)])
def test_reject_unsupported_compressed_codec_and_flags(flags,bpp):
    with pytest.raises(RDPBitmapError):
        decode(wire([rectangle(flags=flags,bpp=bpp)]))


def test_reject_short_header_and_invalid_rle_body():
    for stream in (bytes.fromhex('f3'),bytes.fromhex('62 00'),bytes.fromhex('63 00 00 ff')):
        with pytest.raises(RDPBitmapError):
            decode(wire([rectangle(rle=stream)]))
    with pytest.raises(RDPBitmapError):
        decode(wire([rectangle(flags=1,rle=b'\x01')]))


def test_cannot_publish_first_valid_rect_when_later_is_broken():
    with pytest.raises(RDPBitmapError):
        decode(wire([rectangle(x=0),rectangle(x=3,rle=b'\xff')]))


def test_truncated_mcs_frame_rejected():
    with pytest.raises(RDPBitmapError):
        decode(wire([rectangle()])[:-1])
