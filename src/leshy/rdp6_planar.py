"""Bounded RDP 6.0 RGB(A) and AYCoCg planar bitmap stream decoder.

MS-RDPEGDI 2.2.2.5.1, 3.1.9.1.2-.4 and 3.1.9.2.3. Raw and RLE planes,
CLL 0..7 and optional AYCoCg chroma subsampling are supported. No model
input, login/session assertion, or keyboard/mouse transport is implemented.
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

    Supports lossless RGB/ARGB and lossy AYCoCg (CLL 1..7) with optional
    2x2 chroma subsampling, raw or RDP6 scanline RLE. Alpha must be opaque:
    compositing translucent pixels requires a separate overlay renderer.
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
    if chroma and not cll:
        raise RDP6PlanarError("RDP6 RGB planes cannot use chroma subsampling")
    position = 1
    size = width * height
    # Y and alpha are full-resolution; Co/Cg chroma planes optionally use
    # ceil(width/2) by ceil(height/2), including odd-image edge pixels.
    sub_width, sub_height = (width + 1) // 2, (height + 1) // 2
    dimensions = ([(width, height)] if not no_alpha else []) + [
        (width, height),
        (sub_width, sub_height) if chroma else (width, height),
        (sub_width, sub_height) if chroma else (width, height),
    ]
    planes: list[bytes] = []
    for plane_width, plane_height in dimensions:
        plane_size = plane_width * plane_height
        if use_rle:
            plane, position = _decode_plane_rle(data, position, plane_width, plane_height)
        else:
            if position + plane_size > len(data):
                raise RDP6PlanarError("RDP6 raw color plane truncated")
            plane = data[position:position + plane_size]
            position += plane_size
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

    def signed_chroma(value: int) -> int:
        # The RDP6 chroma byte must be shifted BEFORE interpreting as int8.
        # FreeRDP's reference: (int8_t)(value << (CLL - 1)).  The shift
        # includes division by two from the inverse color transform.
        shifted = (value << (cll - 1)) & 0xff
        return shifted - 256 if shifted >= 128 else shifted

    def clamp8(value: int) -> int:
        return max(0, min(255, value))

    # TS_BITMAP_DATA source scanlines arrive bottom-first. Flip only after
    # fully validating all individual planes and their exact lengths.
    for y in range(height):
        src_y = height - 1 - y
        for x in range(width):
            source = src_y * width + x
            dest = (y * width + x) * 3
            if cll:
                chroma_index = ((src_y // 2) * sub_width + x // 2) if chroma else source
                y_value = red[source]
                co_value = signed_chroma(green[chroma_index])
                cg_value = signed_chroma(blue[chroma_index])
                t = y_value - cg_value
                # MS-RDPEGDI YCoCg inverse in the 8-bit chroma format,
                # consistent with FreeRDP prim_YCoCg.c. Note that source
                # 24bpp B/R channel inversion is a separate server quirk:
                # no metadata here establishes that it applies.
                rgb[dest] = clamp8(t - co_value)
                rgb[dest + 1] = clamp8(y_value + cg_value)
                rgb[dest + 2] = clamp8(t + co_value)
            else:
                rgb[dest] = red[source]
                rgb[dest + 1] = green[source]
                rgb[dest + 2] = blue[source]
    return bytes(rgb)


__all__ = ["RDP6PlanarError", "RDP6PlanarUnsupported", "decompress_rdp6_planar"]
