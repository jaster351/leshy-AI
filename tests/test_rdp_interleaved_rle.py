"""MS-RDPBCGR 16/24-bit Interleaved RLE commands and failure cases."""
import random
import pytest
from leshy.rdp_interleaved_rle import decompress_interleaved_rle, RDPInterleavedError

RED = b'\xff\x00\x00'
GREEN = b'\x00\xff\x00'
BLUE = b'\x00\x00\xff'
WHITE = b'\xff\xff\xff'
BLACK = b'\x00\x00\x00'

@pytest.mark.parametrize('blob,expected',[
    (bytes.fromhex('62 00 00 ff'), RED * 2),
    (bytes.fromhex('82 00 00 ff 00 ff 00'), RED+GREEN),
    (bytes.fromhex('22'), WHITE*2),
    (bytes.fromhex('02'), BLACK*2),
    (bytes.fromhex('fd fe'), WHITE+BLACK),
    (bytes.fromhex('c2 00 00 ff'), RED*2),
    (bytes.fromhex('e1 00 00 ff 00 ff 00'), RED+GREEN),
    (bytes.fromhex('f3 02 00 00 00 ff'), RED*2),
    (bytes.fromhex('f4 02 00 00 00 ff 00 ff 00'), RED+GREEN),
    (bytes.fromhex('f6 02 00 00 00 ff'), RED*2),
    (bytes.fromhex('f8 01 00 00 00 ff 00 ff 00'), RED+GREEN),
    (bytes.fromhex('f0 02 00'), BLACK*2),
    (bytes.fromhex('f1 02 00'), WHITE*2),
    (bytes.fromhex('40 01 01'), WHITE+BLACK),
    (bytes.fromhex('d0 00 00 00 ff 01'), RED),
])
def test_primary_rle_orders(blob,expected):
    assert decompress_interleaved_rle(blob,len(expected)//3,1,24)==expected


def test_fg_bg_mask_lsb_first():
    assert decompress_interleaved_rle(bytes.fromhex('41 05'),8,1,24) == (
        WHITE+BLACK+WHITE+BLACK*5)


def test_special_fgbg_mask_orders():
    assert decompress_interleaved_rle(b'\xf9',8,1,24) == WHITE*2+BLACK*6
    assert decompress_interleaved_rle(b'\xfa',8,1,24) == WHITE+BLACK+WHITE+BLACK*5


def test_two_consecutive_background_runs_insert_fg_on_first_line():
    assert decompress_interleaved_rle(bytes.fromhex('02 02'),4,1,24) == BLACK*2+WHITE+BLACK


def test_bg_no_insertion_when_first_run_ends_at_first_scanline():
    assert decompress_interleaved_rle(bytes.fromhex('02 02'),2,2,24) == BLACK*4


def test_previous_scanline_background_and_foreground():
    assert decompress_interleaved_rle(bytes.fromhex('62 00 00 ff 02'),2,2,24)== RED*4
    assert decompress_interleaved_rle(bytes.fromhex('62 00 00 ff 22'),2,2,24)== b'\x00\xff\xff'*2 + RED*2


def test_16bpp_rgb565_color_run():
    assert decompress_interleaved_rle(bytes.fromhex('62 00 f8'),2,1,16) == RED*2
    assert decompress_interleaved_rle(bytes.fromhex('82 00 f8 1f 00'),2,1,16)==RED+BLUE


def test_regular_extended_color_run_and_megamega():
    assert decompress_interleaved_rle(bytes.fromhex('60 00 00 00 ff'),32,1,24)== RED*32
    assert decompress_interleaved_rle(bytes.fromhex('f3 2c 01 00 00 ff'),300,1,24)== RED*300


@pytest.mark.parametrize('blob',[
    b'', b'\x62', b'\x62\x00\x00', b'\x42\x00\x00\xff',
    b'\xf3', b'\xf3\x01', b'\xf5', b'\xff', b'\xf0\x00\x00',
    b'\xfd\xfd\xfd', b'\xfd\x00', b'\xc0',b'\xd0\x01',
    b'\xe1\x00\x00\xff',b'\xf9', b'\x41',b'\x00',
])
def test_corrupt_or_truncated_stream_fails(blob):
    with pytest.raises(RDPInterleavedError):
        decompress_interleaved_rle(blob,2,1,24)


@pytest.mark.parametrize('args',[
    (0,1,24), (1,0,24), (4097,1,24), (1,4097,24),
    (10,10,32),(10,10,8),(10,10,15),(True,1,24),(1,True,24),
    (2000,2000,24),
])
def test_unsupported_sizing_and_depths(args):
    with pytest.raises(RDPInterleavedError):
        decompress_interleaved_rle(b'\xfd',*args)


def test_fgbg_mask_and_row_crossing_retain_previous():
    stream=bytes.fromhex('62 00 00 ff 40 01 02')
    assert decompress_interleaved_rle(stream,2,2,24) == (RED + b'\x00\xff\xff' + RED*2)


def test_large_short_source_bound():
    with pytest.raises(RDPInterleavedError):
        decompress_interleaved_rle(b'\xfd'*16385,1,1,24)


@pytest.mark.parametrize('blob, width, expected', [
    (bytes.fromhex('f2 08 00 55'), 8, WHITE+BLACK+WHITE+BLACK+WHITE+BLACK+WHITE+BLACK),
    (bytes.fromhex('f7 08 00 00 00 ff aa'), 8, BLACK+RED+BLACK+RED+BLACK+RED+BLACK+RED),
    (bytes.fromhex('d1 00 00 ff aa'), 8, BLACK+RED+BLACK+RED+BLACK+RED+BLACK+RED),
    (bytes.fromhex('f9'), 8, WHITE*2+BLACK*6),
    (bytes.fromhex('fa'), 8, WHITE+BLACK+WHITE+BLACK*5),
    (bytes.fromhex('f8 02 00 00 00 ff 00 ff 00'),4, RED+GREEN+RED+GREEN),
    (bytes.fromhex('e2 00 00 ff 00 ff 00'),4, RED+GREEN+RED+GREEN),
])
def test_advanced_interleaved_opcode_forms(blob,width,expected):
    assert decompress_interleaved_rle(blob,width,1,24)==expected


def test_rle_input_malformed_stream_has_only_declared_exceptions():
    random_state = random.Random(13623)
    for _ in range(1000):
        data = random_state.randbytes(random_state.randrange(1, 24))
        try:
            rgb = decompress_interleaved_rle(data, 4, 4, 24)
        except RDPInterleavedError:
            pass
        else:
            assert len(rgb) == 4 * 4 * 3
