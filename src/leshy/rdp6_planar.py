"""Bounded RDP 6.0 lossless RGB(A) planar bitmap stream decoder.

MS-RDPEGDI 2.2.2.5.1 and 3.1.9.2.3. Only ARGB/RGB (CLL=0,
CS=0) are supported; YCoCg/chroma subsampling are explicitly rejected.
Neither this module nor its caller authorizes input or claims live RDP E2E.
"""
from __future__ import annotations

MAX_RGB = 8 * 1024 * 1024
MAX_STREAM = 16384


class RDP6PlanarError(ValueError):
    """Malformed or unsupported RDP6 planar bitmap stream."""


class RDP6PlanarUnsupported(RDP6PlanarError):
    """RDP6 color-fidelity or chroma variant not implemented."""


def _decode_plane_rle(data: bytes, position: int, width: int, height: int) -> tuple[bytes, int]:
    """Consume precisely one RDP6_RLE_SEGMENTS plane, bounded by scanlines."""
    output = bytearray(width * height)
    for row in range(height):
        x = 0
        last_delta = 0
        while x < width:
            if position >= len(data):
                raise RDP6PlanarError("RDP6 RLE control byte truncated")
            control = data[position]
            position += 1
            if control == 0:
                raise RDP6PlanarError("RDP6 RLE zero-length segment")
            raw_count, run = control >> 4, control & 15
            if run == 1:
                run, raw_count = 16 + raw_count, 0
            elif run == 2:
                run, raw_count = 32 + raw_count, 0
            if raw_count + run > width - x:
                raise RDP6PlanarError("RDP6 RLE segment crosses scanline")
            if position + raw_count > len(data):
                raise RDP6PlanarError("RDP6 RLE raw data truncated")
            for _ in range(raw_count):
                encoded = data[position]
                position += 1
                last_delta = (-((encoded >> 1) + 1) if encoded & 1 else encoded >> 1) if row else encoded
                previous = output[(row - 1) * width + x] if row else 0
                output[row * width + x] = (previous + last_delta) & 0xff
                x += 1
            for _ in range(run):
                previous = output[(row - 1) * width + x] if row else 0
                output[row * width + x] = (previous + last_delta) & 0xff
                x += 1
    return bytes(output), position


def decompress_rdp6_planar(data: bytes, width: int, height: int) -> bytes:
    """Decode a complete RDP6_BITMAP_STREAM as top-down, tightly packed RGB24.

    Supports lossless RGB/ARGB planes, raw or RDP6 scanline RLE. If an
    alpha plane is supplied, it must be opaque: blending and presentation of
    translucent server pixels are not implemented. Unknown modes fail closed.
    """
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= 4096 or not 1 <= height <= 4096
            or width * height * 3 > MAX_RGB):
        raise RDP6PlanarError("Invalid RDP6 bitmap dimensions")
    if not isinstance(data, bytes) or not 1 < len(data) <= MAX_STREAM:
        raise RDP6PlanarError("Invalid RDP6 bitmap data length")
    header = data[0]
    cll, chroma, use_rle, no_alpha, reserved = (header & 7, bool(header & 8),
                                               bool(header & 16), bool(header & 32),
                                               header & 0xc0)
    if reserved:
        raise RDP6PlanarError("Reserved RDP6 format header bits")
    if cll or chroma:
        raise RDP6PlanarUnsupported("RDP6 YCoCg/color loss/subsampling is not implemented")
    position = 1
    size = width * height
    planes: list[bytes] = []
    for _ in range(3 if no_alpha else 4):
        if use_rle:
            plane, position = _decode_plane_rle(data, position, width, height)
        else:
            if position + size > len(data):
                raise RDP6PlanarError("RDP6 raw color plane truncated")
            plane = data[position:position + size]
            position += size
        planes.append(plane)
    if use_rle:
        if position != len(data):
            raise RDP6PlanarError("Unexpected bytes after RDP6 RLE planes")
    elif len(data) - position not in (0, 1):
        raise RDP6PlanarError("RDP6 raw plane data has invalid padding")
    if not no_alpha:
        alpha, red, green, blue = planes
        if any(value != 0xff for value in alpha):
            raise RDP6PlanarUnsupported("Nonopaque RDP6 alpha requires blending")
    else:
        red, green, blue = planes
    rgb = bytearray(size * 3)
    for i in range(size):
        rgb[i * 3] = red[i]
        rgb[i * 3 + 1] = green[i]
        rgb[i * 3 + 2] = blue[i]
    return bytes(rgb)


__all__ = ["RDP6PlanarError", "RDP6PlanarUnsupported", "decompress_rdp6_planar"]
