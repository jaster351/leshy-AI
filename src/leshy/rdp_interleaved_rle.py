"""Bounded MS-RDPBCGR Interleaved RLE bitmap decoder (16/24-bit only).

This is the bitmap *pixel* compression layer, not MPPC bulk compression,
RDP 6.0 planar compression, authentication, or an RDP input implementation.
Return top-to-bottom RGB24 pixels after consuming the exact source stream.
"""
from __future__ import annotations


class RDPInterleavedError(ValueError):
    """Malformed or unsupported interleaved RLE order stream."""


_MAX_RGB = 8 * 1024 * 1024
_MAX_SOURCE = 16 * 1024


def decompress_interleaved_rle(data: bytes, width: int, height: int, bpp: int) -> bytes:
    """Decode MS-RDPBCGR 2.2.9.1.1.3.1.2.4, 3.1.9 with strict bounds.

    Supports all specified regular/lite/mega-mega and special orders for
    RGB565 (16bpp) and BGR24 (24bpp). The source contains no row padding;
    output is top-down, tightly packed RGB, never a guessed full desktop.
    """
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 4096 or not 1 <= height <= 4096 or
            type(bpp) is not int or bpp not in (16, 24) or
            width * height * 3 > _MAX_RGB):
        raise RDPInterleavedError("Invalid RLE color depth or dimensions")
    if not isinstance(data, bytes) or not 1 <= len(data) <= _MAX_SOURCE:
        raise RDPInterleavedError("Invalid or oversized compressed bitmap data")
    step = bpp // 8
    pixel_count = width * height
    raw = bytearray(pixel_count * step)  # bottom-up, unpadded
    pos = 0
    off = 0
    fg = (1 << bpp) - 1
    previous_bg = False

    def require(n: int) -> bytes:
        nonlocal off
        if n < 0 or off + n > len(data):
            raise RDPInterleavedError("Truncated RLE order data")
        chunk = data[off:off + n]
        off += n
        return chunk

    def pixel() -> int:
        return int.from_bytes(require(step), "little")

    def above(index: int) -> int:
        if index < width:
            return 0
        begin = (index - width) * step
        return int.from_bytes(raw[begin:begin + step], "little")

    def put(value: int) -> None:
        nonlocal pos
        if pos >= pixel_count:
            raise RDPInterleavedError("RLE output exceeds rectangle")
        start = pos * step
        raw[start:start + step] = value.to_bytes(step, "little")
        pos += 1

    while off < len(data):
        header = require(1)[0]
        if header >= 0xF0:
            code = header
            if code in (0xFD, 0xFE, 0xF9, 0xFA):
                run = 1 if code in (0xFD, 0xFE) else 8
            elif code in (0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF6, 0xF7, 0xF8):
                run = int.from_bytes(require(2), "little")
            else:
                raise RDPInterleavedError("Reserved RLE opcode")
        elif header >= 0xC0:
            code = header >> 4
            if code not in (0xC, 0xD, 0xE):
                raise RDPInterleavedError("Unknown lite RLE opcode")
            field = header & 15
            if code == 0xD:
                run = field * 8 if field else require(1)[0] + 1
            else:
                run = field if field else require(1)[0] + 16
        else:
            code = header >> 5
            if code not in (0, 1, 2, 3, 4):
                raise RDPInterleavedError("Unknown regular RLE opcode")
            field = header & 31
            if code == 2:
                run = field * 8 if field else require(1)[0] + 1
            else:
                run = field if field else require(1)[0] + 32
        if run <= 0:
            raise RDPInterleavedError("Zero-length RLE run")

        order_pixels = run * (2 if code in (0xE, 0xF8) else 1)
        if pos + order_pixels > pixel_count:
            raise RDPInterleavedError("RLE run crosses rectangle bounds")
        # No RLE opcode may read or modify a pixel outside this allocation.
        bg_order = code in (0, 0xF0)
        if bg_order:
            # Adjacent background runs insert one foreground pel, except
            # when the first one ends precisely on the first scanline.
            if previous_bg and pos != width:
                put(above(pos) ^ fg)
                run -= 1
            for _ in range(run):
                put(above(pos))
        elif code in (1, 0xF1, 0xC, 0xF6):
            if code in (0xC, 0xF6):
                fg = pixel()
            for _ in range(run):
                put(above(pos) ^ fg)
        elif code in (3, 0xF3):
            color = pixel()
            for _ in range(run):
                put(color)
        elif code in (4, 0xF4):
            for _ in range(run):
                put(pixel())
        elif code in (0xE, 0xF8):
            a, b = pixel(), pixel()
            for _ in range(run):
                put(a)
                put(b)
        elif code in (2, 0xF2, 0xD, 0xF7, 0xF9, 0xFA):
            if code in (0xD, 0xF7):
                fg = pixel()
            mask = (0x03 if code == 0xF9 else 0x05) if code in (0xF9, 0xFA) else None
            for i in range(run):
                if i % 8 == 0 and mask is None:
                    bits = require(1)[0]
                else:
                    bits = mask if mask is not None else bits
                put(above(pos) ^ (fg if (bits >> (i % 8)) & 1 else 0))
        elif code == 0xFD:
            put((1 << bpp) - 1)
        elif code == 0xFE:
            put(0)
        else:
            raise RDPInterleavedError("Unhandled RLE order")
        previous_bg = bg_order

    if pos != pixel_count:
        raise RDPInterleavedError("Incomplete RLE bitmap")

    rgb = bytearray(pixel_count * 3)
    for out_row in range(height):
        src_row = height - 1 - out_row
        for x in range(width):
            start = (src_row * width + x) * step
            dest = (out_row * width + x) * 3
            if bpp == 16:
                value = raw[start] | (raw[start + 1] << 8)
                r5, g6, b5 = (value >> 11) & 31, (value >> 5) & 63, value & 31
                rgb[dest:dest + 3] = bytes((
                    (r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2),
                ))
            else:
                rgb[dest:dest + 3] = raw[start + 2], raw[start + 1], raw[start]
    return bytes(rgb)


__all__ = ["RDPInterleavedError", "decompress_interleaved_rle"]
