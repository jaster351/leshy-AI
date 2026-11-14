"""Standard-library RDP frame encoder compatibility checks."""
from __future__ import annotations

import struct
import zlib

import pytest
from leshy.rdp_frame_codec import encode_frame_png
from leshy.superagent_rdp_session import RDPFrame, RDPSessionController


def unpack_png(png):
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    pos = 8
    parts = {}
    while pos < len(png):
        length = struct.unpack(">I", png[pos:pos + 4])[0]
        kind = png[pos + 4:pos + 8]
        content = png[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])[0]
        assert zlib.crc32(kind + content) & 0xFFFFFFFF == crc
        parts.setdefault(kind, []).append(content)
        pos += length + 12
    return parts


def test_png_roundtrip_rgb_and_rgba():
    for mode, pixel, expected_type in (("RGB", b"\x01\x02\x03", 2), ("RGBA", b"\x01\x02\x03\xff", 6)):
        encoded = encode_frame_png(1, 1, pixel, mode=mode)
        parts = unpack_png(encoded)
        w, h, depth, color, _, _, _ = struct.unpack(">IIBBBBB", parts[b"IHDR"][0])
        assert (w, h, depth, color) == (1, 1, 8, expected_type)
        assert zlib.decompress(parts[b"IDAT"][0]) == b"\x00" + pixel
        assert parts[b"IEND"] == [b""]


@pytest.mark.parametrize("width,height,pixels,mode", [
    (0, 1, b"aaa", "RGB"),
    (1, 1, b"aa", "RGB"),
    (1, 1, b"abc", "BGR"),
    (True, 1, b"abc", "RGB"),
    (8192, 8192, b"x", "RGB"),
])
def test_invalid_frame_rejected(width, height, pixels, mode):
    with pytest.raises(ValueError):
        encode_frame_png(width, height, pixels, mode=mode)


class RGBCapture:
    def connect(self, host, *, expected_tls_sha256):
        return object()

    def capture(self, connection):
        return RDPFrame(1, 1, b"\x01\x02\x03", "RGB")

    def click(self, connection, x, y):
        pass

    def type_text(self, connection, text):
        pass

    def close(self, connection):
        pass


def test_session_can_deliver_png_only_for_verified_owner():
    broker = RDPSessionController(RGBCapture(), authorize=lambda owner, node: (
        None if owner == "owner" else (_ for _ in ()).throw(PermissionError("denied"))
    ))
    sid = broker.open("owner", "192.0.2.20", "a" * 64)
    with pytest.raises(PermissionError):
        broker.observe_png("intruder", sid)
    receipt, png = broker.observe_png("owner", sid)
    assert receipt.width == 1
    assert unpack_png(png)[b"IEND"] == [b""]
    broker.close("owner", sid)
