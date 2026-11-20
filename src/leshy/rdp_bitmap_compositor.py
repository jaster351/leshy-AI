"""Fail-closed RGB surface assembled from verified native RDP bitmap rectangles.

A complete-coverage RGB snapshot is *not* an authenticated UI action receipt.
The compositor does not decode compressed data, infer missing pixels, or
expose a keyboard/mouse transport. Updates are transactional per batch.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Sequence

from .rdp_bitmap_update import RDPBitmapRectangle

_MAX_FRAME_BYTES = 32 * 1024 * 1024
_MAX_RECTANGLES = 64
_MAX_RECT_BYTES = 8 * 1024 * 1024


class RDPCompositorError(ValueError):
    """Invalid bitmap patch or incomplete surface (no pixels published)."""


@dataclass(frozen=True)
class RDPComposedFrame:
    width: int
    height: int
    rgb: bytes
    update_count: int
    full_coverage: bool = True
    input_enabled: bool = False


class RDPBitmapCompositor:
    """One surface per approved TLS/RDP session; no cross-session image reuse."""

    def __init__(self, width: int, height: int):
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= 8192 or not 1 <= height <= 8192
                or width * height * 3 > _MAX_FRAME_BYTES):
            raise RDPCompositorError("Invalid or oversized RGB surface")
        self.width, self.height = width, height
        self._count = width * height
        self._pixels = bytearray(self._count * 3)
        self._covered = bytearray(self._count)
        self._covered_count = 0
        self._updates = 0
        self._lock = threading.RLock()

    @property
    def complete(self) -> bool:
        with self._lock:
            return self._covered_count == self._count

    @property
    def covered_pixels(self) -> int:
        with self._lock:
            return self._covered_count

    def reset(self) -> None:
        """Invalidate previously complete graphics before session reactivation."""
        with self._lock:
            self._pixels = bytearray(self._count * 3)
            self._covered = bytearray(self._count)
            self._covered_count = 0
            self._updates = 0

    def snapshot(self) -> RDPComposedFrame | None:
        """Return immutable RGB only once every pixel was actually received."""
        with self._lock:
            if self._covered_count != self._count:
                return None
            return RDPComposedFrame(self.width, self.height,
                                    bytes(self._pixels), self._updates)

    def apply(self, rectangles: Sequence[RDPBitmapRectangle]) -> RDPComposedFrame | None:
        """Apply one entire validated update atomically; never publish partial.

        All rectangles are checked before *any* mutation. A corrupted second
        patch cannot partly overwrite the first; unknown pixels remain
        inaccessible until real updates cover the entire surface.
        """
        if (not isinstance(rectangles, (tuple, list))
                or not 1 <= len(rectangles) <= _MAX_RECTANGLES):
            raise RDPCompositorError("Invalid RDP rectangle batch")
        with self._lock:
            for rect in rectangles:
                if not isinstance(rect, RDPBitmapRectangle):
                    raise RDPCompositorError("Untrusted rectangle type")
                left, top, w, h = rect.left, rect.top, rect.width, rect.height
                if (any(type(v) is not int for v in (left, top, w, h))
                        or left < 0 or top < 0 or w < 1 or h < 1
                        or left + w > self.width or top + h > self.height
                        or w > 4096 or h > 4096
                        or rect.pixel_format != "RGB"
                        or type(rect.rgb) is not bytes
                        or len(rect.rgb) != w * h * 3
                        or len(rect.rgb) > _MAX_RECT_BYTES):
                    raise RDPCompositorError("Malformed or unsupported RGB patch")

            pixels = self._pixels.copy()
            covered = self._covered.copy()
            count = self._covered_count
            for rect in rectangles:
                for row in range(rect.height):
                    dest = ((rect.top + row) * self.width) + rect.left
                    end = dest + rect.width
                    src = row * rect.width * 3
                    pixels[dest * 3:end * 3] = rect.rgb[src:src + rect.width * 3]
                    previous = covered[dest:end]
                    count += rect.width - previous.count(1)
                    covered[dest:end] = b"\x01" * rect.width
            self._pixels = pixels
            self._covered = covered
            self._covered_count = count
            self._updates += 1
            if count != self._count:
                return None
            return RDPComposedFrame(self.width, self.height, bytes(pixels), self._updates)


__all__ = ["RDPCompositorError", "RDPComposedFrame", "RDPBitmapCompositor"]
