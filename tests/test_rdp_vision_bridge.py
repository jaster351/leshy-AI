"""Isolated in-process RDP PNG sink tests: no real sockets, login or LLM."""
from __future__ import annotations

import hashlib
import pytest

from leshy.rdp_bitmap_compositor import RDPComposedFrame
from leshy.rdp_credssp_channel import PinnedRDPCredSSPChannel
from leshy.rdp_graphics_stream import GraphicsStreamResult
from leshy.rdp_vision_bridge import stream_native_rdp_png


class FakeFinalizedChannel(PinnedRDPCredSSPChannel):
    """Only models the already authenticated, read-only frame callback."""

    def __init__(self, frames):
        self.frames = frames
        self.closed = False
        self.stopped = False
        self.seen = 0

    def stream_bitmap_frames(self, on_frame, *, policy=None):
        assert policy is not None
        try:
            for frame in self.frames:
                if self.stopped:
                    break
                self.seen += 1
                on_frame(frame)
            reason = "stopped" if self.stopped else "update_limit"
            return GraphicsStreamResult(self.seen, self.seen, reason, 0.01)
        finally:
            self.close()

    def stop_graphics_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


def frame(data=b"\x01\x02\x03\x04\x05\x06", update=1, **changes):
    values = dict(width=2, height=1, rgb=data, update_count=update)
    values.update(changes)
    return RDPComposedFrame(**values)


def test_real_decoded_rgb_is_encoded_only_in_memory_without_input_grant():
    transport = FakeFinalizedChannel([frame()])
    observations = []
    checks = []
    result = stream_native_rdp_png(
        transport, observations.append, reauthorize=lambda: checks.append("owner")
    )
    assert transport.closed
    assert len(checks) >= 3
    assert result.png_delivered == 1 and result.unchanged_frames_skipped == 0
    assert not result.input_enabled and not result.desktop_verified
    obs = observations[0]
    assert obs.width == 2 and obs.height == 1
    assert obs.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert obs.content_sha256 == hashlib.sha256(
        (2).to_bytes(4, "big") + (1).to_bytes(4, "big") + frame().rgb
    ).hexdigest()
    assert not obs.input_enabled
    assert not hasattr(obs, "frame_digest")


def test_identical_frames_suppressed_without_storing_screenshots():
    transport = FakeFinalizedChannel([
        frame(update=1), frame(update=2), frame(b"\x06\x05\x04\x03\x02\x01", 3)
    ])
    images = []
    result = stream_native_rdp_png(
        transport, images.append, reauthorize=lambda: None
    )
    assert [f.update_count for f in images] == [1, 3]
    assert result.png_delivered == 2 and result.unchanged_frames_skipped == 1
    assert transport.closed


def test_max_png_frames_requests_owner_stream_stop():
    transport = FakeFinalizedChannel([
        frame(update=1), frame(b"\x06\x05\x04\x03\x02\x01", 2),
        frame(b"\x03\x04\x05\x06\x01\x02", 3),
    ])
    images = []
    result = stream_native_rdp_png(
        transport, images.append, reauthorize=lambda: None, max_png_frames=2
    )
    assert result.png_delivered == 2 and transport.seen == 2
    assert transport.stopped and transport.closed


@pytest.mark.parametrize("bad", [
    frame(update=0),
    frame(update=True),
    frame(data=b"bad"),
    frame(full_coverage=False),
    frame(input_enabled=True),
    frame(width=True),
    frame(rgb=bytearray(b"123456")),
])
def test_malformed_or_actionable_frame_fails_closed(bad):
    transport = FakeFinalizedChannel([bad])
    images = []
    with pytest.raises(ValueError):
        stream_native_rdp_png(
            transport, images.append, reauthorize=lambda: None
        )
    assert images == [] and transport.closed


def test_out_of_order_update_count_is_rejected_without_second_image():
    transport = FakeFinalizedChannel([frame(update=2), frame(update=1)])
    images = []
    with pytest.raises(ValueError):
        stream_native_rdp_png(
            transport, images.append, reauthorize=lambda: None
        )
    assert len(images) == 1 and transport.closed


def test_owner_revoked_after_encoding_never_receives_png(monkeypatch):
    import leshy.rdp_vision_bridge as bridge
    transport = FakeFinalizedChannel([frame()])
    original = bridge.encode_frame_png
    approved = [True]

    def encode(*args, **kwargs):
        png = original(*args, **kwargs)
        approved[0] = False
        return png

    monkeypatch.setattr(bridge, "encode_frame_png", encode)

    def authorize():
        if not approved[0]:
            raise PermissionError("revoked")

    images = []
    with pytest.raises(PermissionError, match="revoked"):
        stream_native_rdp_png(transport, images.append, reauthorize=authorize)
    assert images == [] and transport.closed


def test_sink_error_closes_without_retry():
    transport = FakeFinalizedChannel([frame()])
    def unavailable(_):
        raise RuntimeError("vision consumer down")
    with pytest.raises(RuntimeError):
        stream_native_rdp_png(transport, unavailable, reauthorize=lambda: None)
    assert transport.seen == 1 and transport.closed


@pytest.mark.parametrize("limit", [0, 257, True, 0.5])
def test_invalid_delivery_limit_closes_channel(limit):
    transport = FakeFinalizedChannel([frame()])
    with pytest.raises(ValueError):
        stream_native_rdp_png(
            transport, lambda _: None, reauthorize=lambda: None,
            max_png_frames=limit,
        )
    assert transport.closed and transport.seen == 0


def test_same_bytes_at_different_dimensions_are_distinct_observations():
    transport = FakeFinalizedChannel([
        frame(update=1),
        frame(width=1, height=2, update_count=2),
    ])
    images = []
    result = stream_native_rdp_png(
        transport, images.append, reauthorize=lambda: None
    )
    assert len(images) == 2 and result.unchanged_frames_skipped == 0
    assert images[0].content_sha256 != images[1].content_sha256
