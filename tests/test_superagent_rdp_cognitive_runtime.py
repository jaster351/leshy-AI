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
