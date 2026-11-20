"""Transactional full-coverage compositor tests, no real Windows server."""
import pytest

from leshy.rdp_bitmap_update import RDPBitmapRectangle
from leshy.rdp_bitmap_compositor import (
    RDPBitmapCompositor, RDPCompositorError,
)


def rect(x, y, width, height, rgb, *, fmt="RGB"):
    return RDPBitmapRectangle(x, y, width, height, rgb, fmt)


def test_partial_bitmap_does_not_publish_invented_frame():
    comp = RDPBitmapCompositor(2, 2)
    assert comp.snapshot() is None
    assert comp.apply((rect(0, 0, 2, 1, b"\x10\x11\x12" * 2),)) is None
    assert comp.covered_pixels == 2
    assert not comp.complete
    assert comp.snapshot() is None


def test_complete_frame_exact_pixels_and_partial_overwrite():
    comp = RDPBitmapCompositor(2, 2)
    assert comp.apply((rect(0, 0, 1, 2, b"\xff\0\0\x00\x00\xff"),)) is None
    frame = comp.apply((rect(1, 0, 1, 2, b"\0\xff\0\xff\xff\xff"),))
    assert comp.complete and comp.covered_pixels == 4
    assert (frame.width, frame.height, frame.update_count) == (2, 2, 2)
    assert frame.rgb == bytes.fromhex("ff0000 00ff00 0000ff ffffff")
    assert frame.full_coverage and not frame.input_enabled
    changed = comp.apply((rect(0, 1, 1, 1, b"\x01\x02\x03"),))
    assert changed.rgb == bytes.fromhex("ff0000 00ff00 010203 ffffff")
    assert changed.update_count == 3
    assert frame.rgb == bytes.fromhex("ff0000 00ff00 0000ff ffffff")


def test_multi_rect_batch_atomic_and_duplicate_coverage():
    comp = RDPBitmapCompositor(2, 1)
    assert comp.apply([rect(0, 0, 1, 1, b"\x01\x02\x03"),
                       rect(0, 0, 1, 1, b"\x04\x05\x06")]) is None
    assert comp.covered_pixels == 1
    assert comp.apply((rect(1, 0, 1, 1, b"\x07\x08\x09"),)).rgb == (
        b"\x04\x05\x06\x07\x08\x09")


def test_bad_later_patch_rolls_back_whole_update():
    comp = RDPBitmapCompositor(2, 1)
    assert comp.apply((rect(0, 0, 1, 1, b"\x01\x02\x03"),)) is None
    with pytest.raises(RDPCompositorError):
        comp.apply((rect(1, 0, 1, 1, b"\x07\x08\x09"),
                    rect(0, 0, 2, 1, b"\x01")))
    assert comp.covered_pixels == 1 and not comp.complete
    assert comp.snapshot() is None
    assert comp.apply((rect(1, 0, 1, 1, b"\x07\x08\x09"),)).rgb == (
        b"\x01\x02\x03\x07\x08\x09")


@pytest.mark.parametrize("bad", [
    rect(-1, 0, 1, 1, b"123"), rect(0, -1, 1, 1, b"123"),
    rect(2, 0, 1, 1, b"123"), rect(0, 2, 1, 1, b"123"),
    rect(0, 0, 0, 1, b""), rect(0, 0, 1, 0, b""),
    rect(0, 0, 1, 1, b"12"), rect(0, 0, 1, 1, b"1234"),
    rect(0, 0, 1, 1, bytearray(b"123")),
    rect(0, 0, True, 1, b"123"),
    rect(0, 0, 1, 1, b"123", fmt="opaque"),
    object(),
])
def test_bad_patch_fails_closed(bad):
    comp = RDPBitmapCompositor(2, 2)
    with pytest.raises(RDPCompositorError):
        comp.apply((bad,))
    assert comp.snapshot() is None and comp.covered_pixels == 0


@pytest.mark.parametrize("size", [(0, 1), (1, 0), (8193, 1),
                                  (1, 8193), (4096, 4096),
                                  (True, 1), (1, 1.5)])
def test_surface_limits(size):
    with pytest.raises(RDPCompositorError):
        RDPBitmapCompositor(*size)


def test_reset_invalidates_complete_snapshot():
    comp = RDPBitmapCompositor(1, 1)
    assert comp.apply([rect(0, 0, 1, 1, b"abc")]) is not None
    comp.reset()
    assert not comp.complete and comp.snapshot() is None
    assert comp.covered_pixels == 0


def test_empty_or_large_batches_rejected():
    comp = RDPBitmapCompositor(1, 1)
    for bad in ([], tuple(), [rect(0, 0, 1, 1, b"abc")] * 65):
        with pytest.raises(RDPCompositorError):
            comp.apply(bad)


def test_multiple_rows_with_strides_preserve_boundaries():
    comp = RDPBitmapCompositor(3, 2)
    frame = comp.apply([rect(0, 0, 3, 2,
                             bytes.fromhex("010203 040506 070809"
                                           " 0a0b0c 0d0e0f 101112"))])
    assert frame.rgb == bytes.fromhex("010203 040506 070809 0a0b0c 0d0e0f 101112")
    assert comp.complete
