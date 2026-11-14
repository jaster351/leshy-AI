"""RDP broker tests use an in-memory fake; never connect to a real desktop."""
from __future__ import annotations

import pytest

from leshy.superagent_rdp_session import RDPFrame, RDPSessionController


class FakeBackend:
    def __init__(self):
        self.clicks = []
        self.texts = []
        self.closed = []
        self.connected = []

    def connect(self, host, *, expected_tls_sha256):
        self.connected.append((host, expected_tls_sha256))
        return object()

    def capture(self, connection):
        return RDPFrame(800, 600, b"test-image-bytes")

    def click(self, connection, x, y):
        self.clicks.append((x, y))

    def type_text(self, connection, text):
        self.texts.append(text)

    def close(self, connection):
        self.closed.append(connection)


def setup_controller():
    backend = FakeBackend()
    now = [100.0]
    allowed = {"alice"}
    def authorize(owner, node):
        if owner not in allowed or node != "192.0.2.10":
            raise PermissionError("No permission")
    controller = RDPSessionController(
        backend, authorize=authorize, clock=lambda: now[0], max_frame_age=5,
    )
    return controller, backend, now, allowed


def test_authorized_observe_then_click_once():
    broker, backend, now, allowed = setup_controller()
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    frame = broker.observe("alice", sid)
    assert frame.width == 800
    assert frame.height == 600
    broker.click("alice", sid, frame_digest=frame.frame_digest, x=40, y=50)
    assert backend.clicks == [(40, 50)]
    with pytest.raises(PermissionError):
        broker.click("alice", sid, frame_digest=frame.frame_digest, x=40, y=50)
    broker.close("alice", sid)
    assert len(backend.closed) == 1


def test_wrong_owner_and_unenrolled_tls_refused():
    broker, backend, _, _ = setup_controller()
    with pytest.raises(PermissionError):
        broker.open("alice", "192.0.2.10", "wrong")
    assert not backend.connected
    sid = broker.open("alice", "192.0.2.10", "b" * 64)
    with pytest.raises(PermissionError):
        broker.observe("bob", sid)
    assert not backend.clicks


def test_stale_frame_and_bounds():
    broker, backend, now, _ = setup_controller()
    sid = broker.open("alice", "192.0.2.10", "c" * 64)
    frame = broker.observe("alice", sid)
    now[0] += 6
    with pytest.raises(PermissionError):
        broker.click("alice", sid, frame_digest=frame.frame_digest, x=10, y=10)
    assert backend.clicks == []
    new_frame = broker.observe("alice", sid)
    with pytest.raises(ValueError):
        broker.click("alice", sid, frame_digest=new_frame.frame_digest, x=800, y=10)
    assert backend.clicks == []


def test_revoked_permission_blocks_action():
    broker, backend, _, allowed = setup_controller()
    sid = broker.open("alice", "192.0.2.10", "d" * 64)
    frame = broker.observe("alice", sid)
    allowed.clear()
    with pytest.raises(PermissionError):
        broker.click("alice", sid, frame_digest=frame.frame_digest, x=1, y=1)
    assert not backend.clicks


def test_session_limit():
    broker, backend, _, _ = setup_controller()
    broker.max_sessions = 1
    broker.open("alice", "192.0.2.10", "e" * 64)
    with pytest.raises(RuntimeError):
        broker.open("alice", "192.0.2.10", "f" * 64)
    assert len(backend.connected) == 1


def test_owner_can_close_after_revocation():
    broker, backend, _, allowed = setup_controller()
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    allowed.clear()
    broker.close("alice", sid)
    assert len(backend.closed) == 1


def test_observe_frame_and_unique_receipt_for_identical_pixels():
    broker, _, _, _ = setup_controller()
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    first, pixels = broker.observe_frame("alice", sid)
    second, pixels2 = broker.observe_frame("alice", sid)
    assert pixels == pixels2 == b"test-image-bytes"
    assert first.frame_digest != second.frame_digest
    with pytest.raises(PermissionError):
        broker.click("alice", sid, frame_digest=first.frame_digest, x=1, y=1)


def test_text_input_requires_new_receipt_and_owner():
    broker, backend, _, _ = setup_controller()
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    receipt, _ = broker.observe_frame("alice", sid)
    with pytest.raises(PermissionError):
        broker.type_text("bob", sid, frame_digest=receipt.frame_digest, text="hello")
    broker.type_text("alice", sid, frame_digest=receipt.frame_digest, text="hello")
    assert backend.texts == ["hello"]
    with pytest.raises(PermissionError):
        broker.type_text("alice", sid, frame_digest=receipt.frame_digest, text="again")


def test_text_validation_and_revocation():
    broker, backend, _, allowed = setup_controller()
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    receipt = broker.observe("alice", sid)
    for bad in ("", "a" * 513, "bad\\nvalue", "bad\\x00value"):
        with pytest.raises(ValueError):
            broker.type_text("alice", sid, frame_digest=receipt.frame_digest, text=bad)
    allowed.clear()
    with pytest.raises(PermissionError):
        broker.type_text("alice", sid, frame_digest=receipt.frame_digest, text="safe")
    assert backend.texts == []


class ChangingScreenBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.pixels = b"initial-frame"
        self.capture_count = 0
        self.on_capture = None

    def capture(self, connection):
        self.capture_count += 1
        if self.on_capture is not None:
            self.on_capture(self.capture_count)
        return RDPFrame(800, 600, self.pixels)


def test_changed_desktop_blocks_click_without_dispatch():
    broker, _, now, _ = setup_controller()
    backend = ChangingScreenBackend()
    broker.backend = backend
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    receipt = broker.observe("alice", sid)
    backend.pixels = b"screen-changed"
    with pytest.raises(PermissionError, match="desktop changed"):
        broker.click("alice", sid, frame_digest=receipt.frame_digest, x=10, y=10)
    assert backend.clicks == []
    assert backend.capture_count == 2
    with pytest.raises(PermissionError):
        broker.click("alice", sid, frame_digest=receipt.frame_digest, x=10, y=10)


def test_changed_desktop_blocks_keyboard_input():
    broker, _, _, _ = setup_controller()
    backend = ChangingScreenBackend()
    broker.backend = backend
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    receipt = broker.observe("alice", sid)
    backend.pixels = b"screen-changed"
    with pytest.raises(PermissionError, match="desktop changed"):
        broker.type_text("alice", sid, frame_digest=receipt.frame_digest, text="hello")
    assert backend.texts == []


def test_revocation_during_recapture_blocks_click():
    broker, _, _, allowed = setup_controller()
    backend = ChangingScreenBackend()
    backend.on_capture = lambda count: allowed.clear() if count == 2 else None
    broker.backend = backend
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    receipt = broker.observe("alice", sid)
    with pytest.raises(PermissionError):
        broker.click("alice", sid, frame_digest=receipt.frame_digest, x=2, y=3)
    assert backend.clicks == []


def test_timeout_during_recapture_blocks_click():
    broker, _, now, _ = setup_controller()
    backend = ChangingScreenBackend()
    backend.on_capture = lambda count: now.__setitem__(0, 106.0) if count == 2 else None
    broker.backend = backend
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    receipt = broker.observe("alice", sid)
    with pytest.raises(PermissionError, match="desktop changed"):
        broker.click("alice", sid, frame_digest=receipt.frame_digest, x=2, y=3)
    assert backend.clicks == []


def test_unchanged_desktop_allows_click_after_recapture():
    broker, _, _, _ = setup_controller()
    backend = ChangingScreenBackend()
    broker.backend = backend
    sid = broker.open("alice", "192.0.2.10", "a" * 64)
    receipt = broker.observe("alice", sid)
    broker.click("alice", sid, frame_digest=receipt.frame_digest, x=2, y=3)
    assert backend.clicks == [(2, 3)]
    assert backend.capture_count == 2
