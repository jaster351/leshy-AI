"""Minimal standard-library PNG encoding for in-memory native RDP screenshots.

Input is tightly specified RGB24/RGBA32 pixels in row-major order. Backend
decoding and RDP authentication belong to the transport, not this encoder.
"""
from __future__ import annotations

import struct
import zlib


def encode_frame_png(width: int, height: int, pixels: bytes, *, mode: str = "RGB") -> bytes:
    if type(width) is not int or type(height) is not int:
        raise ValueError("Frame dimensions must be integers")
    if not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError("Frame dimensions out of bounds")
    channels = {"RGB": 3, "RGBA": 4}.get(mode)
    if channels is None:
        raise ValueError("Only RGB and RGBA pixel buffers are supported")
    size = width * height * channels
    if size > 32 * 1024 * 1024:
        raise ValueError("Frame exceeds memory policy")
    if not isinstance(pixels, bytes) or len(pixels) != size:
        raise ValueError("Pixel buffer size does not match dimensions and format")

    def chunk(kind: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value)) + kind + value +
            struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    stride = width * channels
    raw = b"".join(b"\x00" + pixels[pos:pos + stride] for pos in range(0, len(pixels), stride))
    header = struct.pack(">IIBBBBB", width, height, 8, 2 if channels == 3 else 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", header) +
        chunk(b"IDAT", zlib.compress(raw, level=6)) +
        chunk(b"IEND", b"")
    )


__all__ = ["encode_frame_png"]
