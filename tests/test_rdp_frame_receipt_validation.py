"""RDP receipt regressions: malformed captures never authorize remote input."""
from __future__ import annotations

import pytest

from leshy.superagent_rdp_session import RDPFrame, RDPSessionController


class CaptureBackend:
    def __init__(self):
        self.frame = RDPFrame(2, 2, bytes(12), "RGB")
        self.clicks = []

    def connect(self, host, *, expected_tls_sha256):
        return object()

    def capture(self, connection):
        return self.frame

    def click(self, connection, x, y):
        self.clicks.append((x, y))

    def type_text(self, connection, text):
        pass

    def close(self, connection):
        pass


def test_malformed_rgb_frame_never_issues_receipt():
    backend = CaptureBackend()
    controller = RDPSessionController(backend, authorize=lambda owner, node: None)
    sid = controller.open("owner", "192.0.2.1", "a" * 64)
    backend.frame = RDPFrame(2, 2, bytes(11), "RGB")
    with pytest.raises(ValueError, match="size"):
        controller.observe_frame("owner", sid)
    assert not backend.clicks


def test_failed_recapture_invalidates_previous_receipt():
    backend = CaptureBackend()
    controller = RDPSessionController(backend, authorize=lambda owner, node: None)
    sid = controller.open("owner", "192.0.2.1", "a" * 64)
    receipt = controller.observe("owner", sid)
    backend.frame = RDPFrame(2, 2, bytes(11), "RGB")
    with pytest.raises(ValueError, match="size"):
        controller.observe("owner", sid)
    backend.frame = RDPFrame(2, 2, bytes(12), "RGB")
    with pytest.raises(PermissionError):
        controller.click("owner", sid, frame_digest=receipt.frame_digest, x=0, y=0)
    assert not backend.clicks


def test_valid_rgb_receipt_remains_actionable():
    backend = CaptureBackend()
    controller = RDPSessionController(backend, authorize=lambda owner, node: None)
    sid = controller.open("owner", "192.0.2.1", "a" * 64)
    receipt = controller.observe("owner", sid)
    controller.click("owner", sid, frame_digest=receipt.frame_digest, x=1, y=1)
    assert backend.clicks == [(1, 1)]
