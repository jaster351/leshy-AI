"""Atomic RDP observation receipts: revocation, interrupted capture and PNG races.

Uses only an in-memory decoded RGB backend. No real network or RDP login.
"""
from __future__ import annotations

import pytest

from leshy.superagent_rdp_session import RDPFrame, RDPSessionController


class RGBBackend:
    def __init__(self):
        self.frame = RDPFrame(2, 2, b"\x01" * 12, "RGB")
        self.fail = False
        self.clicks = []

    def connect(self, host, *, expected_tls_sha256):
        return object()

    def capture(self, conn):
        if self.fail:
            raise TimeoutError("capture interrupted")
        return self.frame

    def click(self, conn, x, y):
        self.clicks.append((x, y))

    def type_text(self, conn, text):
        raise AssertionError("unexpected input")

    def close(self, conn):
        pass


def setup():
    backend, granted = RGBBackend(), [True]
    def authorize(owner, node):
        if not granted[0] or owner != "alice" or node != "owned.example":
            raise PermissionError("owner grant revoked")
    controller = RDPSessionController(backend, authorize=authorize)
    sid = controller.open("alice", "owned.example", "a" * 64)
    return controller, backend, sid, granted


def test_revoked_observation_spends_old_receipt_even_if_rights_return():
    controller, backend, sid, granted = setup()
    old = controller.observe("alice", sid)
    granted[0] = False
    with pytest.raises(PermissionError):
        controller.observe_frame("alice", sid)
    granted[0] = True
    with pytest.raises(PermissionError, match="already used"):
        controller.click("alice", sid, frame_digest=old.frame_digest, x=1, y=1)
    assert backend.clicks == []


def test_interrupted_capture_never_reactivates_last_frame():
    controller, backend, sid, granted = setup()
    old = controller.observe("alice", sid)
    backend.fail = True
    with pytest.raises(TimeoutError):
        controller.observe_frame("alice", sid)
    backend.fail = False
    with pytest.raises(PermissionError, match="already used"):
        controller.click("alice", sid, frame_digest=old.frame_digest, x=1, y=1)
    assert not backend.clicks


def test_invalid_image_format_fails_without_partially_publishing_receipt():
    controller, backend, sid, granted = setup()
    original = controller.observe("alice", sid)
    state = controller._sessions[sid]
    backend.frame = RDPFrame(2, 2, b"\x01" * 12, "☃")
    with pytest.raises(ValueError, match="pixel format"):
        controller.observe_frame("alice", sid)
    assert state.frame_digest == original.frame_digest
    assert state.used is True
    backend.frame = RDPFrame(2, 2, b"\x01" * 12, "RGB")
    with pytest.raises(PermissionError, match="already used"):
        controller.click("alice", sid, frame_digest=original.frame_digest, x=0, y=0)


def test_png_reauthorization_failure_consumes_new_receipt():
    controller, backend, sid, granted = setup()
    original_get = controller._get

    def revoke_after_capture(owner_id, session_id):
        granted[0] = False
        return original_get(owner_id, session_id)

    controller._get = revoke_after_capture
    with pytest.raises(PermissionError):
        controller.observe_png("alice", sid)
    granted[0] = True
    assert controller._sessions[sid].used is True
    receipt = controller._sessions[sid].frame_digest
    with pytest.raises(PermissionError, match="already used"):
        controller.click("alice", sid, frame_digest=receipt, x=0, y=0)
    assert backend.clicks == []


def test_other_owner_cannot_consume_legitimate_receipt():
    controller, backend, sid, granted = setup()
    old = controller.observe("alice", sid)
    with pytest.raises(PermissionError):
        controller.observe_frame("bob", sid)
    controller.click("alice", sid, frame_digest=old.frame_digest, x=1, y=1)
    assert backend.clicks == [(1, 1)]
