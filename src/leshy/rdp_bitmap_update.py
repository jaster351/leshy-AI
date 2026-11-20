"""Read-only slow-path RDP Bitmap Update decoding.

MS-RDPBCGR 2.2.9.1.1.3.1.2.1-.2.4: an enhanced-TLS,
non-fragmented MCS SendDataIndication carrying TS_UPDATE_BITMAP_DATA.
This module returns *rectangles*; it does not invent a complete desktop,
implement RDP6 planar compression, or grant remote input permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .rdp_server_activation import RDPActivationError, read_server_mcs_data
from .rdp_interleaved_rle import RDPInterleavedError, decompress_interleaved_rle
from .rdp6_planar import RDP6PlanarError, RDP6PlanarUnsupported, decompress_rdp6_planar

_MAX_RECTANGLES = 64
_MAX_RECT_SIDE = 4096
_MAX_RECT_RGB = 8 * 1024 * 1024


class RDPBitmapError(ValueError):
    """Bad or unsupported server bitmap; no untrusted pixels were published."""


class RDPBitmapUnsupported(RDPBitmapError):
    """Real server format not yet implemented; never guess image bytes."""


@dataclass(frozen=True)
class RDPBitmapRectangle:
    left: int
    top: int
    width: int
    height: int
    rgb: bytes  # validated top-down, left-right, tightly packed RGB24
    pixel_format: str = "RGB"


def _decode_rgb(bottom_up: bytes, width: int, height: int, bpp: int) -> bytes:
    bytes_per_pixel = bpp // 8
    unpadded = width * bytes_per_pixel
    stride = (unpadded + 3) & ~3
    if len(bottom_up) != stride * height:
        raise RDPBitmapError("Bitmap row alignment or length invalid")
    if width * height * 3 > _MAX_RECT_RGB:
        raise RDPBitmapError("Decoded bitmap would exceed memory bound")
    rgb = bytearray(width * height * 3)
    for row in range(height):
        src = (height - 1 - row) * stride
        dst = row * width * 3
        for x in range(width):
            offset = src + x * bytes_per_pixel
            if bpp == 16:  # negotiated 16bpp RGB565, little-endian
                value = bottom_up[offset] | (bottom_up[offset + 1] << 8)
                r5, g6, b5 = (value >> 11) & 31, (value >> 5) & 63, value & 31
                rgb[dst:dst + 3] = bytes((
                    (r5 << 3) | (r5 >> 2),
                    (g6 << 2) | (g6 >> 4),
                    (b5 << 3) | (b5 >> 2),
                ))
            else:  # BGR24 or BGRX32; ignore fourth/alpha byte
                rgb[dst:dst + 3] = (
                    bottom_up[offset + 2],
                    bottom_up[offset + 1],
                    bottom_up[offset],
                )
            dst += 3
    return bytes(rgb)


def parse_bitmap_update(
    packet: bytes,
    *,
    share_id: int,
    server_user_id: int,
    io_channel_id: int,
    desktop_width: int,
    desktop_height: int,
) -> tuple[RDPBitmapRectangle, ...]:
    """Validate and decode a single bounded slow-path Bitmap Update.

    Coordinates are inclusive in RDP but rectangles returned here are
    conventional (left, top, width, height). No partial/unknown coverage is
    silently filled with black pixels. Full framebuffer assembly is NOT done.
    """
    if (type(share_id) is not int or not 0 < share_id <= 0xffffffff
            or type(server_user_id) is not int or not 1001 <= server_user_id <= 65535
            or type(desktop_width) is not int or type(desktop_height) is not int
            or not 1 <= desktop_width <= 8192 or not 1 <= desktop_height <= 8192):
        raise RDPBitmapError("Invalid trusted session dimensions or identity")
    try:
        source, data = read_server_mcs_data(
            packet, expected_io_channel=io_channel_id,
        )
    except RDPActivationError as exc:
        raise RDPBitmapError("Invalid bitmap MCS envelope") from exc
    if source != server_user_id or len(data) < 22:
        raise RDPBitmapError("Bitmap sender or share data missing")
    total, share_type, origin = struct.unpack_from("<HHH", data, 0)
    sid, _pad, stream_id, plain_size, kind, compressed, compressed_size = (
        struct.unpack_from("<IBBHBBH", data, 6)
    )
    if (total != len(data) or share_type != 0x0017
            or origin != server_user_id or sid != share_id
            or kind != 0x02 or stream_id not in (1, 2)
            or compressed or compressed_size
            or plain_size != len(data) - 18 + 4):
        raise RDPBitmapError("Invalid or compressed RDP Share Data header")
    update_type, count = struct.unpack_from("<HH", data, 18)
    if update_type != 1 or not 1 <= count <= _MAX_RECTANGLES:
        raise RDPBitmapUnsupported("Only bounded slow-path bitmap updates are supported")
    pos = 22
    rectangles: list[RDPBitmapRectangle] = []
    for _ in range(count):
        if pos + 18 > len(data):
            raise RDPBitmapError("Truncated bitmap rectangle header")
        (left, top, right, bottom, width, height, bpp, flags,
         size) = struct.unpack_from("<9H", data, pos)
        pos += 18
        # BITMAP_COMPRESSION (0x0001) and NO_BITMAP_COMPRESSION_HDR
        # (0x0400) are the only flags implemented; without compression
        # the latter has no meaning. At 32bpp the stream uses RDP6 planar
        # compression; it must NEVER be passed to Interleaved RLE.
        if flags & ~0x0401 or flags == 0x0400:
            raise RDPBitmapUnsupported("Unsupported RDP bitmap flags")
        if bpp not in (16, 24, 32):
            raise RDPBitmapUnsupported("Unsupported bitmap depth")
        if (not width or not height or width > _MAX_RECT_SIDE or height > _MAX_RECT_SIDE
                or left > right or top > bottom
                or width != right - left + 1 or height != bottom - top + 1
                or right >= desktop_width or bottom >= desktop_height):
            # Clipped vs source-sized bitmaps need separate clipping support.
            raise RDPBitmapUnsupported("Unsupported clipping or bitmap bounds")
        if pos + size > len(data):
            raise RDPBitmapError("Bitmap pixel bytes truncated")
        stride = ((width * (bpp // 8) + 3) & ~3)
        if width * height * 3 > _MAX_RECT_RGB:
            raise RDPBitmapError("Bitmap allocation exceeds limits")
        if flags & 0x0001:
            compressed = data[pos:pos + size]
            if not flags & 0x0400:
                # TS_CD_HEADER: first-row size MUST be zero. The main
                # body size excludes this eight-byte header.
                if size < 8:
                    raise RDPBitmapError("Truncated compressed bitmap header")
                first, main, scan, raw_size = struct.unpack_from("<4H", compressed)
                if (first != 0 or main != size - 8
                        or scan < width or scan % 4
                        or raw_size not in (stride * height, width * height * (bpp // 8))):
                    raise RDPBitmapError("Invalid RDP compressed bitmap header")
                compressed = compressed[8:]
            if bpp == 32:
                try:
                    rgb = decompress_rdp6_planar(compressed, width, height)
                except RDP6PlanarUnsupported as exc:
                    raise RDPBitmapUnsupported("Unsupported RDP6 planar color mode") from exc
                except RDP6PlanarError as exc:
                    raise RDPBitmapError("Invalid RDP6 planar bitmap stream") from exc
            else:
                try:
                    rgb = decompress_interleaved_rle(compressed, width, height, bpp)
                except RDPInterleavedError as exc:
                    raise RDPBitmapError("Invalid RDP Interleaved RLE stream") from exc
        else:
            if size != stride * height:
                raise RDPBitmapError("Bitmap stride or allocation exceeds limits")
            rgb = _decode_rgb(data[pos:pos + size], width, height, bpp)
        rectangles.append(RDPBitmapRectangle(left, top, width, height, rgb))
        pos += size
    if pos != len(data):
        raise RDPBitmapError("Trailing bitmap update bytes")
    return tuple(rectangles)


__all__ = [
    "RDPBitmapError", "RDPBitmapUnsupported", "RDPBitmapRectangle",
    "parse_bitmap_update",
]
