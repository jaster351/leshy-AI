"""SuperAgent ticket authorization tests without network, DB or Windows RDP."""
from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest

from leshy.rdp_local_vision import RDPVisualObservation
from leshy.superagent_remote_permissions import VerifiedActor
from leshy.superagent_rdp_cognitive_runtime import SuperAgentRDPCognitiveSession


def observation(num=1):
    return RDPVisualObservation(
        "Открыто окно ошибки", 800, 600, "a" * 64, num, "local-vision"
    )


class FakeRemotePolicy:
    permitted = True
    checked = 0

    def __init__(self, identity):
        assert identity is not None

    def _check_actor(self, actor):
        if not isinstance(actor, VerifiedActor) or actor.method != "web":
            raise PermissionError("unverified")
        self.__class__.checked += 1

    def require(self, actor, node, capability, *, observed_fingerprint):
        self._check_actor(actor)
        if not self.__class__.permitted or node != "10.0.0.2" or capability != "rdp":
            raise PermissionError("revoked")
        if observed_fingerprint != "registered-host-pin":
            raise PermissionError("host mismatch")


class FakeService:
    def __init__(self, reply='{"kind":"report","reason":"Проверено изображение"}'):
        self.app = SimpleNamespace(owner_identity=object())
        self._stop = threading.Event()
        self.state = "working"
        self.task = "Проверь удалённый рабочий стол"
        self.reply = reply
        self.model_calls = 0
        self.messages = None

    def get(self, ticket_id, *, owner_id):
        if owner_id != "owner" or ticket_id != "SA-001":
            raise PermissionError("wrong owner")
        return {"state": self.state, "request_text": self.task}

    def _chat_inference(self, ticket_id, messages):
        self.model_calls += 1
        self.messages = messages
        return self.reply


@pytest.fixture(autouse=True)
def reset_policy(monkeypatch):
    import leshy.superagent_rdp_cognitive_runtime as module
    FakeRemotePolicy.permitted = True
    FakeRemotePolicy.checked = 0
    monkeypatch.setattr(module, "RemotePermissionPolicy", FakeRemotePolicy)


def start(service=None, actor=None, node_id="10.0.0.2", pin="registered-host-pin"):
    return SuperAgentRDPCognitiveSession(
        service or FakeService(),
        ticket_id="SA-001",
        actor=actor or VerifiedActor("owner", "live-session", "web"),
        node_id=node_id, observed_fingerprint=pin,
    )


def test_real_superagent_model_receives_ticket_task_and_untrusted_observation():
    service = FakeService()
    session = start(service)
    assert session.accept_observation(observation())
    result = session.reason_once()
    assert result.kind == "report"
    assert not result.input_enabled and result.execution_status == "not_executed"
    assert not result.owner_approval_issued
    assert service.model_calls == 1
    assert [x.role for x in service.messages] == ["system", "user", "tool"]
    assert service.task in service.messages[1].content
    assert FakeRemotePolicy.checked >= 3


def test_ticket_stop_revokes_pending_observations_even_without_session_stop():
    service = FakeService()
    session = start(service)
    session.accept_observation(observation())
    service.state = "stopped"
    with pytest.raises(PermissionError):
        session.reason_once()
    assert service.model_calls == 0


def test_new_ticket_state_rejected_at_creation():
    service = FakeService()
    service.state = "waiting"
    with pytest.raises(PermissionError):
        start(service)


def test_forged_identity_and_unregistered_node_are_rejected():
    with pytest.raises(PermissionError):
        start(actor="owner")
    with pytest.raises(PermissionError):
        start(node_id="10.0.0.3")
    with pytest.raises(PermissionError):
        start(pin="other-pin")


def test_permission_revoke_after_inference_denies_result():
    service = FakeService()
    def reply(_):
        FakeRemotePolicy.permitted = False
        return '{"kind":"report","reason":"Окно"}'
    service._chat_inference = lambda tid, msgs: reply(msgs)
    session = start(service)
    session.accept_observation(observation())
    with pytest.raises(PermissionError):
        session.reason_once()


def test_owner_task_must_not_change_during_runtime():
    service = FakeService()
    session = start(service)
    service.task = "Новая неавторизованная задача"
    with pytest.raises(PermissionError):
        session.accept_observation(observation())


def test_stop_cancels_pending_and_new_visual_results():
    session = start()
    session.accept_observation(observation())
    session.stop()
    assert session.snapshot()["stopped"]
    with pytest.raises(PermissionError):
        session.reason_once()
    with pytest.raises(PermissionError):
        session.accept_observation(observation(2))


def test_stale_visual_result_is_not_dispatched_as_a_tool_action():
    service = FakeService('{"kind":"propose_click","reason":"Кнопка","x":10,"y":20}')
    session = start(service)
    session.accept_observation(observation())
    result = session.reason_once()
    assert (result.x, result.y) == (10, 20)
    assert not result.owner_approval_issued
    assert not result.input_enabled
    assert not hasattr(result, "execute")


def test_existing_superagent_service_exposes_internal_factory():
    from leshy.superagent import SuperAgentService
    service = FakeService()
    session = SuperAgentService.create_rdp_cognitive_session(
        service, "SA-001",
        actor=VerifiedActor("owner", "live-session", "web"),
        node_id="10.0.0.2", observed_fingerprint="registered-host-pin",
    )
    assert isinstance(session, SuperAgentRDPCognitiveSession)



def test_native_cognition_loop_uses_real_superagent_llm_without_rdp_actions():
    import time
    service = FakeService('{"kind":"report","reason":"По изображению видна ошибка"}')
    session = start(service)
    # Normal production starts the consumer from receive_graphics, after
    # the pinned channel is attached. Mock the private thread gate here
    # to verify genuine LLM consumption, rather than a fake plan.
    session.start_cognitive_worker()
    session.accept_observation(observation())
    end = time.monotonic() + 2
    while not session.snapshot()["cognitive_completed"] and time.monotonic() < end:
        time.sleep(.01)
    assert service.model_calls == 1
    assert session.snapshot()["cognitive_completed"] == 1
    assert session.snapshot()["cognitive_error_type"] == ""
    session.stop()


def test_native_cognition_stops_on_untrusted_llm_action():
    import time
    service = FakeService('{"kind":"shell","command":"format disk"}')
    session = start(service)
    session.start_cognitive_worker()
    session.accept_observation(observation())
    end = time.monotonic() + 2
    while not session.snapshot()["stopped"] and time.monotonic() < end:
        time.sleep(.01)
    assert session.snapshot()["stopped"] is True
    assert session.snapshot()["cognitive_error_type"] == "RDPCognitiveError"
    assert service.model_calls == 1



def test_cognitive_consumer_exits_after_native_graphics_finishes():
    import time
    session = start()
    session._graphics_started = True
    session._channel = object()
    session.start_cognitive_worker()
    worker = session._cognitive_thread
    assert worker is not None
    session._channel = None
    session._cognitive_wake.set()
    worker.join(timeout=2)
    assert not worker.is_alive()


def test_proposal_is_not_exposed_without_a_fresh_covered_frame():
    import time
    session = start()
    session.accept_observation(observation(1))
    proposal = session.reason_once()
    assert proposal is not None
    # No real decoded native framebuffer has arrived: model text may exist,
    # but cannot be shown as verified actionable desktop evidence.
    assert session.last_proposal() is None
    session._channel = object()
    session._web_png = object()
    session._web_png_at = time.monotonic()
    session._last_web_update = 1
    assert session.last_proposal() is proposal
    session._web_png_at -= 20
    assert session.last_proposal() is None



def test_failure_purges_real_png_cache_and_untrusted_model_proposal():
    import time
    session = start()
    session._channel = object()
    session._native_phase = "graphics"
    session._web_png = object()
    session._web_png_at = time.monotonic()
    session._last_web_update = 77
    session._last_proposal = object()
    session.native_failure("TimeoutError")
    snap = session.snapshot()
    assert snap["native_phase"] == "failed"
    assert snap["native_error_type"] == "TimeoutError"
    assert session._web_png is None
    assert session._web_png_at == 0.0
    assert session._last_web_update == 0
    assert session.last_proposal() is None


def test_failure_preserves_original_error_class_through_cleanup_failure():
    session = start()
    session.native_failure("CredSSPBindingError")
    session.native_failure("RDPTransportCloseError")
    assert session.snapshot()["native_phase"] == "failed"
    assert session.snapshot()["native_error_type"] == "CredSSPBindingError"


@pytest.mark.parametrize("bad", [None, 12, True, object()])
def test_failure_class_never_serializes_exception_body(bad):
    session = start()
    session.native_failure(bad)
    assert session.snapshot()["native_error_type"] == "Exception"


def test_failure_status_cannot_resurrect_after_owner_stop():
    session = start()
    session.stop()
    session.native_failure("RDPTransportCloseError")
    assert session.snapshot()["native_phase"] == "stopped"
    assert session.snapshot()["stopped"]


@pytest.mark.parametrize("untrusted_error", [
    "x" * 300, "Error: password", "TLS\r\nAuthorization: Bearer abc",
    "/home/user/private.json", "Ошибка_пароля", "Failed Session",
    "CredSSP\0Token", "https://example.org/?password=hidden",
])
def test_failure_error_type_rejects_messages_and_private_data(untrusted_error):
    session = start()
    session.native_failure(untrusted_error)
    assert session.snapshot()["native_error_type"] == "Exception"


@pytest.mark.parametrize("known_type", [
    "TimeoutError", "SSLError", "CredSSPBindingError", "RDPTransportCloseError",
])
def test_failure_error_type_preserves_safe_class_names(known_type):
    session = start()
    session.native_failure(known_type)
    assert session.snapshot()["native_error_type"] == known_type



def _native_frame(update=1):
    from leshy.rdp_frame_codec import encode_frame_png
    from leshy.rdp_vision_bridge import RDPVisionPNG
    pixels = bytes.fromhex("010203040506")
    return RDPVisionPNG(2, 1, encode_frame_png(2, 1, pixels), "a" * 64, update)


def test_failed_phase_drops_late_real_png_instead_of_resurrecting_screen():
    session = start()
    session._channel = object()
    session._native_phase = "graphics"
    session.accept_native_png(_native_frame())
    assert session.snapshot()["frame_available"]
    session.native_failure("TimeoutError")
    assert session.current_native_png() is None
    with pytest.raises(PermissionError, match="terminal"):
        session.accept_native_png(_native_frame(2))
    status = session.snapshot()
    assert status["native_phase"] == "failed"
    assert not status["graphics_active"]
    assert not status["frame_available"]
    assert status["frame_update"] == 0


@pytest.mark.parametrize("terminal", ["failed", "stream_finished", "stopped"])
def test_terminal_native_phase_rejects_new_vision_observation(terminal):
    session = start()
    session._native_phase = terminal
    with pytest.raises(PermissionError, match="terminal"):
        session.accept_observation(observation())
    assert session._inbox.snapshot()["pending"] is False


@pytest.mark.parametrize("terminal", ["failed", "stream_finished", "stopped"])
def test_terminal_native_phase_prevents_new_model_inference(terminal):
    service = FakeService()
    session = start(service)
    session._native_phase = terminal
    assert session.reason_once() is None
    assert service.model_calls == 0


@pytest.mark.parametrize("terminal", ["failed", "stream_finished"])
def test_terminal_phase_cannot_revert_to_graphics_or_send_fake_completion(terminal):
    session = start()
    session._native_phase = terminal
    with pytest.raises(PermissionError, match="terminal"):
        session.set_native_phase("graphics")
    with pytest.raises(PermissionError, match="terminal"):
        session.set_native_phase("stream_finished")
    assert session.snapshot()["native_phase"] == terminal


def test_finished_stream_revokes_last_real_frame_and_planner_proposal():
    session = start()
    session._native_phase = "graphics"
    session._channel = object()
    frame = _native_frame(1)
    session.accept_native_png(frame)
    session._last_proposal = object()
    session.set_native_phase("stream_finished")
    assert session.snapshot()["native_phase"] == "stream_finished"
    assert session._web_png is None
    assert session._last_proposal is None
    assert session.current_native_png() is None
    assert not session.snapshot()["frame_available"]
    with pytest.raises(PermissionError, match="terminal"):
        session.accept_native_png(_native_frame(2))


def test_native_failure_wakes_and_retires_idle_cognitive_worker_without_model_calls():
    import time
    service = FakeService()
    session = start(service)
    session.start_cognitive_worker()
    worker = session._cognitive_thread
    assert worker is not None
    session.native_failure("TimeoutError")
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert service.model_calls == 0
    assert session.snapshot()["cognitive_completed"] == 0


def test_native_failure_mid_model_call_drops_late_reasoning_output():
    import time
    service = FakeService()
    entered, release = threading.Event(), threading.Event()
    original = service._chat_inference

    def slow_inference(ticket, messages):
        entered.set()
        assert release.wait(timeout=2)
        return original(ticket, messages)

    service._chat_inference = slow_inference
    session = start(service)
    session._native_phase = "graphics"
    session.start_cognitive_worker()
    try:
        session.accept_observation(observation())
        assert entered.wait(timeout=2)
        session.native_failure("TimeoutError")
    finally:
        release.set()
    worker = session._cognitive_thread
    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert service.model_calls == 1
    assert session.snapshot()["cognitive_completed"] == 0
    assert session._last_proposal is None


def test_native_failure_after_stream_finish_does_not_republish_frame():
    session = start()
    session._native_phase = "graphics"
    session._channel = object()
    session.accept_native_png(_native_frame())
    session.set_native_phase("stream_finished")
    session.native_failure("RDPTransportCloseError")
    assert session.snapshot()["native_phase"] == "failed"
    assert session.current_native_png() is None
    with pytest.raises(PermissionError):
        session.accept_observation(observation())



@pytest.mark.parametrize("terminal", ["failed", "stream_finished", "stopped"])
def test_terminal_phase_refuses_new_cognitive_worker(terminal):
    session = start()
    session._native_phase = terminal
    with pytest.raises(PermissionError, match="unavailable"):
        session.start_cognitive_worker()
    assert session._cognitive_thread is None


@pytest.mark.parametrize("terminal", ["failed", "stream_finished", "stopped"])
def test_terminal_phase_refuses_new_verified_handshake(terminal, monkeypatch):
    import leshy.superagent_rdp_cognitive_runtime as module
    class FakeVerifiedChannel:
        pass
    monkeypatch.setattr(module, "PinnedRDPCredSSPChannel", FakeVerifiedChannel)
    session = start()
    session._native_phase = terminal
    with pytest.raises(PermissionError, match="no longer allowed"):
        session.attach_native_handshake(FakeVerifiedChannel())
    assert session._handshake_channel is None


@pytest.mark.parametrize("terminal", ["failed", "stream_finished", "stopped"])
def test_terminal_phase_cannot_start_second_graphics_stream(terminal, monkeypatch):
    import leshy.superagent_rdp_cognitive_runtime as module
    class FakeVerifiedChannel:
        pass
    monkeypatch.setattr(module, "PinnedRDPCredSSPChannel", FakeVerifiedChannel)
    session = start()
    session._native_phase = terminal
    with pytest.raises(PermissionError, match="unavailable"):
        session.receive_graphics(FakeVerifiedChannel())
    assert session._channel is None
    assert session._graphics_started is False
