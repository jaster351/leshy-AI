"""RDP PNG Web read boundary: authenticated real frames only (no network)."""
from __future__ import annotations

from types import SimpleNamespace
import threading
import time

import pytest

from leshy.rdp_frame_codec import encode_frame_png
from leshy.rdp_vision_bridge import RDPVisionPNG
from leshy.superagent_remote_permissions import VerifiedActor
from leshy.superagent_rdp_live_registry import RDPLiveRegistry
from leshy.superagent_rdp_cognitive_runtime import SuperAgentRDPCognitiveSession


class Policy:
    def __init__(self, identity):
        assert identity is not None
    def _check_actor(self, actor):
        if not isinstance(actor, VerifiedActor) or actor.method != "web":
            raise PermissionError("bad cookie")


class Service:
    def __init__(self):
        self.app = SimpleNamespace(owner_identity=object())
    def get(self, ticket_id, *, owner_id):
        if (ticket_id, owner_id) != ("SA-1", "alice"):
            raise PermissionError("wrong ticket")
        return {"state": "working"}


def actor(cookie="session"):
    return VerifiedActor("alice", cookie, "web")


def image(num=1):
    rgb = bytes.fromhex("010203040506")
    return RDPVisionPNG(2, 1, encode_frame_png(2, 1, rgb), "a" * 64, num)


class FakeChannel:
    def __init__(self):
        self.stop_requested = False
        self.closed = False

    def stop_graphics_stream(self):
        self.stop_requested = True

    def close(self):
        self.closed = True


def setup(monkeypatch):
    import leshy.superagent_rdp_live_registry as reg
    monkeypatch.setattr(reg, "RemotePermissionPolicy", Policy)
    session = object.__new__(SuperAgentRDPCognitiveSession)
    session._actor = actor()
    session._ticket_id = "SA-1"
    session._node_id = "10.0.0.5"
    session._lock = threading.RLock()
    session._cancel = threading.Event()
    session._channel = FakeChannel()
    session._handshake_channel = None
    session._native_phase = "graphics"
    session._native_error_type = ""
    session._graphics_started = True
    session._latest_frame_update = 0
    session._last_proposal = None
    session._web_png = None
    session._web_png_at = 0.0
    session._last_web_update = 0
    session._inbox = SimpleNamespace(
        stop=lambda: None, snapshot=lambda: {"stopped": False, "busy": False,
                                              "pending": False, "dropped": 0}
    )
    session.reauthorize = lambda: (
        (_ for _ in ()).throw(PermissionError("stopped"))
        if session._cancel.is_set() else None
    )
    registry = RDPLiveRegistry(Service())
    registry.register(actor(), "SA-1", session)
    return registry, session


def test_never_returns_a_frame_before_real_native_callback(monkeypatch):
    registry, session = setup(monkeypatch)
    images = []
    assert not registry.transmit_png(actor(), "SA-1", lambda png, n: images.append(n))
    assert images == []
    assert not registry.status(actor(), "SA-1")["screen_available"]


def test_native_png_web_frame_is_actual_decoded_producer_png(monkeypatch):
    registry, session = setup(monkeypatch)
    frame = image(1)
    session.accept_native_png(frame)
    delivered = []
    assert registry.status(actor(), "SA-1")["screen_available"]
    assert registry.status(actor(), "SA-1")["frame_update"] == 1
    assert registry.transmit_png(actor(), "SA-1", lambda body, n: delivered.append((body, n)))
    assert delivered == [(frame.png, frame.update_count)]


def test_png_is_replaced_not_accumulated(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    newer = image(2)
    session.accept_native_png(newer)
    assert session.current_native_png() == newer
    with pytest.raises(ValueError):
        session.accept_native_png(image(1))


def test_old_frame_has_expired_no_web_pixels(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    session._web_png_at = time.monotonic() - 20
    assert session.current_native_png() is None
    assert not registry.transmit_png(actor(), "SA-1", lambda *_: None)
    assert not registry.status(actor(), "SA-1")["screen_available"]


def test_stop_revokes_frame_and_suppresses_followup_delivery(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    assert registry.stop(actor(), "SA-1")["stopped"] is True
    assert session._web_png is None
    assert session._channel.stop_requested
    assert session._channel.closed
    assert not registry.transmit_png(actor(), "SA-1", lambda *_: None)


def test_no_cross_cookie_access_to_binary_png(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    with pytest.raises(PermissionError):
        registry.transmit_png(actor("another-session"), "SA-1", lambda *_: None)


def test_status_and_frame_do_not_depend_on_model_proposals(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    assert registry.status(actor(), "SA-1")["reasoning_available"] is False
    assert registry.transmit_png(actor(), "SA-1", lambda data, n: None)


def test_web_png_route_is_cookied_binary_and_disabled_for_img_tag():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = (root / "src/leshy/web_server_impl14.py").read_text(encoding="utf-8")
    js = (root / "web/superagent-dashboard.js").read_text(encoding="utf-8")
    assert 'parsed.path == "/v1/superagent/rdp/frame"' in code
    assert 'self.headers.get("X-Leshy-UI") != "superagent"' in code
    assert 'self._remote_context()' in code
    assert 'self.send_header("Content-Type", "image/png")' in code
    assert 'self.send_header("Cache-Control", "private, no-store, max-age=0")' in code
    assert "URL.createObjectURL(blob)" in js
    assert "URL.revokeObjectURL" in js
    assert "screen_available" in js
    assert "screen.addEventListener('click'" not in js



def test_worker_forwards_only_accepted_native_frame(monkeypatch):
    import leshy.rdp_vision_worker as module

    class FakeWorker:
        def __init__(self, **kwargs):
            self._submit_ok = True

        def start(self):
            return None

        def submit(self, frame):
            return self._submit_ok

        def drain(self, **kwargs):
            return True

        def stop(self):
            return None

        def status(self):
            return SimpleNamespace(failure_type="")

    class Channel:
        closed = False

        def close(self):
            self.closed = True

    captured = []
    orig_frame = image(1)

    def fake_stream(channel, on_png, **kwargs):
        on_png(orig_frame)
        return SimpleNamespace()

    monkeypatch.setattr(module, "RDPVisionWorker", FakeWorker)
    monkeypatch.setattr(module, "stream_native_rdp_png", fake_stream)
    ch = Channel()
    result = module.stream_native_rdp_with_local_vision(
        ch, reauthorize=lambda: None,
        on_observation=lambda _: None,
        on_png_frame=captured.append,
    )
    assert result.drained
    assert captured == [orig_frame]
    assert ch.closed


def test_rejected_worker_frame_never_enters_web_cache(monkeypatch):
    import leshy.rdp_vision_worker as module

    class RejectWorker:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def submit(self, frame):
            return False

        def stop(self):
            pass

    class Channel:
        closed = False

        def close(self):
            self.closed = True

    def fake_stream(channel, on_png, **kwargs):
        on_png(image(1))

    monkeypatch.setattr(module, "RDPVisionWorker", RejectWorker)
    monkeypatch.setattr(module, "stream_native_rdp_png", fake_stream)
    captured = []
    ch = Channel()
    with pytest.raises(RuntimeError, match="stopped accepting"):
        module.stream_native_rdp_with_local_vision(
            ch, reauthorize=lambda: None,
            on_observation=lambda _: None,
            on_png_frame=captured.append,
        )
    assert captured == []
    assert ch.closed



def test_web_fetch_is_cancellable_and_hides_pixels_on_background_tabs():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] /
          "web/superagent-dashboard.js").read_text(encoding="utf-8")
    assert "new AbortController()" in js
    assert "signal: controller.signal" in js
    assert "frameAbort.abort()" in js
    assert "document.visibilityState !== 'visible'" in js
    assert "const releaseOld" in js


def test_png_stream_never_sends_json_after_headers_are_sent():
    from pathlib import Path
    code = (Path(__file__).resolve().parents[1] /
            "src/leshy/web_server_impl14.py").read_text(encoding="utf-8")
    assert "response_started = True" in code
    assert "if response_started:" in code



def test_stop_closes_active_tls_transport_even_if_graceful_stop_throws(monkeypatch):
    registry, session = setup(monkeypatch)
    class ErrorChannel(FakeChannel):
        def stop_graphics_stream(self):
            self.stop_requested = True
            raise OSError("socket receiver already torn down")
    channel = ErrorChannel()
    session._channel = channel
    session.accept_native_png(image(1))
    with pytest.raises(OSError):
        registry.stop(actor(), "SA-1")
    assert channel.closed
    assert session._web_png is None
    with pytest.raises(RuntimeError):
        registry.register(actor(), "SA-1", session)



def test_web_frame_is_revoked_on_native_failure_without_ticket_stop(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    assert registry.status(actor(), "SA-1")["screen_available"]
    session.native_failure("RDPChannelError")
    status = registry.status(actor(), "SA-1")
    assert status["native_phase"] == "failed"
    assert status["native_error_type"] == "RDPChannelError"
    assert not status["screen_available"]
    assert status["frame_update"] == 0
    assert not registry.transmit_png(actor(), "SA-1", lambda *_: None)


def test_web_failure_hides_last_model_proposal(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    session._last_proposal = object()
    session.native_failure("RDPChannelError")
    assert session._last_proposal is None
    assert registry.status(actor(), "SA-1")["reasoning_available"] is False



def test_native_failure_rejects_late_web_png_callback(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    session.native_failure("TimeoutError")
    with pytest.raises(PermissionError, match="terminal"):
        session.accept_native_png(image(2))
    assert session._web_png is None
    seen = []
    assert not registry.transmit_png(actor(), "SA-1", lambda *_: seen.append(1))
    assert seen == []


def test_native_finished_phase_rejects_new_png_and_blanks_browser(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    session.set_native_phase("stream_finished")
    with pytest.raises(PermissionError, match="terminal"):
        session.accept_native_png(image(2))
    assert session.current_native_png() is None
    status = registry.status(actor(), "SA-1")
    assert status["native_phase"] == "stream_finished"
    assert not status["screen_available"]
    assert status["frame_update"] == 0


def test_stale_png_is_never_transmitted_when_error_arrives_between_checks(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    original = session.current_native_png
    def fail_while_reading():
        frame = original()
        session.native_failure("RDPChannelError")
        return frame
    session.current_native_png = fail_while_reading
    delivered = []
    assert not registry.transmit_png(actor(), "SA-1", lambda *_: delivered.append(1))
    assert delivered == []
    assert registry.status(actor(), "SA-1")["status"] == "native_failed"


def test_native_failure_keeps_web_reporting_original_class_only(monkeypatch):
    registry, session = setup(monkeypatch)
    session.accept_native_png(image(1))
    session.native_failure("TimeoutError")
    session.native_failure("RDPTransportCloseError")
    status = registry.status(actor(), "SA-1")
    assert status["native_error_type"] == "TimeoutError"
    assert status["status"] == "native_failed"
    assert status["frame_update"] == 0
    assert not status["connected"]



def test_hidden_browser_aborts_png_fetch_before_and_after_network_response():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] /
          "web/superagent-dashboard.js").read_text(encoding="utf-8")
    assert "if (frameBusy || !panel.open || !panel.isConnected" in js
    assert "|| document.visibilityState !== 'visible') return;" in js
    assert "const blob = await response.blob()" in js


def test_native_rdp_view_revokes_pixels_on_visibility_change_without_poll_delay():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] /
          "web/superagent-dashboard.js").read_text(encoding="utf-8")
    assert "const onRdpVisibility = () => {" in js
    assert "document.addEventListener('visibilitychange', onRdpVisibility)" in js
    assert "document.removeEventListener('visibilitychange', onRdpVisibility)" in js
    assert "if (document.visibilityState !== 'visible') clearScreen()" in js


def test_hidden_tab_suppresses_owner_rdp_status_and_does_not_reveal_stale_frame():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] /
          "web/superagent-dashboard.js").read_text(encoding="utf-8")
    assert "if (document.visibilityState !== 'visible') {\n        clearScreen();\n        return;\n      }" in js
    assert "|| document.visibilityState !== 'visible') return;" in js
