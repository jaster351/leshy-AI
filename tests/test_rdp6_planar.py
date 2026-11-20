"""RDP6 planar RGB/ARGB and plane-RLE test vectors."""
import random
import pytest
from leshy.rdp6_planar import (
    RDP6PlanarError, RDP6PlanarUnsupported,
    decompress_rdp6_planar, _decode_plane_rle,
)


def test_raw_rgb_pixels_with_no_alpha_and_pad():
    # Red, green, blue, white; planar scan lines in stream order.
    stream = bytes([0x20]) + bytes([255, 0, 0, 255]) + bytes([0, 255, 0, 255]) + bytes([0, 0, 255, 255])
    expected = bytes.fromhex("ff0000 00ff00 0000ff ffffff")
    assert decompress_rdp6_planar(stream, 2, 2) == expected
    assert decompress_rdp6_planar(stream + b"\xae", 2, 2) == expected


def test_raw_argb_with_opaque_alpha():
    stream = b"\x00" + b"\xff\xff" + b"\x12\x34" + b"\x56\x78" + b"\x9a\xbc"
    assert decompress_rdp6_planar(stream, 2, 1) == bytes.fromhex("12569a 3478bc")


def test_rle_plane_run_and_advanced_lengths():
    # 1 RAW and 3 RUN values, repeated three color planes.
    stream = bytes([0x30]) + b"\x13\xff" + b"\x13\x00" + b"\x13\x00"
    assert decompress_rdp6_planar(stream, 4, 1) == bytes.fromhex("ff0000") * 4
    assert _decode_plane_rle(bytes([0xf1]), 0, 31, 1)[0] == b"\x00" * 31
    assert _decode_plane_rle(bytes([0xf2]), 0, 47, 1)[0] == b"\x00" * 47


def test_rle_multiscanline_delta_according_to_microsoft_published_example():
    encoded = bytes.fromhex("13 ff 20 fe fd 60 01 7d f5 c2 9a 38 60 01 67 8b a3 78 af")
    got, consumed = _decode_plane_rle(encoded, 0, 6, 3)
    assert consumed == len(encoded)
    assert list(got) == [
        255, 255, 255, 255, 254, 253, 254, 192, 132, 96, 75, 25,
        253, 140, 62, 14, 135, 193,
    ]


def test_rle_three_planes_and_pixel_order():
    stream = (b"\x30" + (b"\x30\x12\x12\x12\x03")
              + (b"\x30\x34\x34\x34\x03")
              + (b"\x30\x56\x56\x56\x03"))
    assert decompress_rdp6_planar(stream, 3, 2) == bytes.fromhex("123456") * 6


def test_raw_alpha_transparency_rejected():
    with pytest.raises(RDP6PlanarUnsupported):
        decompress_rdp6_planar(b"\0\0\xff\xff\xff", 1, 1)


@pytest.mark.parametrize("header", [0x01, 0x08, 0x09, 0x11, 0x28, 0x40, 0x80, 0xff])
def test_unsupported_or_reserved_header(header):
    with pytest.raises(RDP6PlanarError):
        decompress_rdp6_planar(bytes([header]) + b"\0" * 64, 1, 1)


@pytest.mark.parametrize("blob", [
    b"", b"\x20", b"\x20\x01\x02", b"\x20" + b"\0" * 5,
    b"\x30\0", b"\x30\xf1", b"\x30\x10",
    b"\x20\x01\x02\x03\x04\x05", b"\x30\x13",
])
def test_invalid_truncated_planes(blob):
    with pytest.raises(RDP6PlanarError):
        decompress_rdp6_planar(blob, 2, 1)


@pytest.mark.parametrize("dimensions", [
    (0, 1), (1, 0), (True, 1), (1, True), (4097, 1),
    (1, 4097), (4096, 4096), (1.1, 1),
])
def test_invalid_dimensions(dimensions):
    with pytest.raises(RDP6PlanarError):
        decompress_rdp6_planar(b"\x20\0" * 10, *dimensions)


@pytest.mark.parametrize("blob", [b"\0", b"\x10", b"\x01", b"\x20"])
def test_invalid_rle_segment_controls(blob):
    with pytest.raises(RDP6PlanarError):
        _decode_plane_rle(blob, 0, 4, 1)


def test_fuzz_bounded_malformed_packets_never_raise_unexpected():
    rng = random.Random(6210)
    for _ in range(400):
        length = rng.randrange(2, 90)
        stream = bytes([rng.choice((0, 16, 32, 48))]) + rng.randbytes(length - 1)
        try:
            result = decompress_rdp6_planar(stream, 3, 2)
        except RDP6PlanarError:
            continue
        assert len(result) == 18
