"""Read-only RDP live registry; no real Web, DB, sockets or model."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leshy.superagent_remote_permissions import VerifiedActor
from leshy.superagent_rdp_live_registry import RDPLiveRegistry


class AuthPolicy:
    def __init__(self, identity):
        assert identity is not None

    def _check_actor(self, actor):
        if not isinstance(actor, VerifiedActor) or actor.method != "web":
            raise PermissionError("invalid actor")


class Service:
    def __init__(self):
        self.app = SimpleNamespace(owner_identity=object())
        self.state = "working"

    def get(self, ticket_id, *, owner_id):
        if ticket_id != "SA-1" or owner_id != "alice":
            raise PermissionError("foreign ticket")
        return {"id": ticket_id, "owner_id": owner_id, "state": self.state}


class Session:
    def __init__(self, actor, ticket_id="SA-1"):
        self._actor = actor
        self._ticket_id = ticket_id
        self.live = True
        self.stopped = False
        self.proposal = None

    def reauthorize(self):
        if not self.live or self.stopped:
            raise PermissionError("revoked")

    def snapshot(self):
        return {
            "graphics_active": True, "node_id": "10.0.0.5",
            "ticket_id": self._ticket_id, "stopped": self.stopped,
        }

    def last_proposal(self):
        return self.proposal

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def auth(monkeypatch):
    import leshy.superagent_rdp_live_registry as module
    monkeypatch.setattr(module, "RemotePermissionPolicy", AuthPolicy)


def actor(cookie="cookie-a"):
    return VerifiedActor("alice", cookie, "web")


def test_no_live_session_does_not_fake_a_connected_rdp_desktop():
    service = Service()
    controls = RDPLiveRegistry(service)
    record = controls.status(actor(), "SA-1")
    assert record["registered"] is False and record["connected"] is False
    assert not record["screen_available"] and not record["input_enabled"]
    assert record["proposal"] is None


def test_register_live_session_reports_read_only_state():
    service = Service()
    controls = RDPLiveRegistry(service)
    user = actor()
    session = Session(user)
    controls.register(user, "SA-1", session)
    record = controls.status(user, "SA-1")
    assert record["registered"] is True and record["connected"] is True and record["graphics_active"] is True
    assert record["node_id"] == "10.0.0.5"
    assert record["screen_available"] is False and record["e2e_verified"] is False
    assert record["input_enabled"] is False


def test_other_browser_cookie_cannot_read_or_stop_same_owner_session():
    controls = RDPLiveRegistry(Service())
    original = actor()
    session = Session(original)
    controls.register(original, "SA-1", session)
    with pytest.raises(PermissionError):
        controls.status(actor("different-cookie"), "SA-1")
    with pytest.raises(PermissionError):
        controls.stop(actor("different-cookie"), "SA-1")
    assert not session.stopped


def test_owner_stop_is_idempotent_and_survives_rdp_grant_revocation():
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    controls.register(user, "SA-1", session)
    session.live = False
    record = controls.stop(user, "SA-1")
    assert record["stopped"] is True and session.stopped
    assert controls.stop(user, "SA-1")["stopped"] is False


def test_internal_stop_ticket_revokes_existing_session():
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    controls.register(user, "SA-1", session)
    controls.stop_ticket("alice", "SA-1")
    assert session.stopped and not controls.status(user, "SA-1")["connected"]


def test_ticket_mismatch_and_duplicate_registry_are_denied():
    controls = RDPLiveRegistry(Service())
    user = actor()
    controls.register(user, "SA-1", Session(user))
    with pytest.raises(RuntimeError):
        controls.register(user, "SA-1", Session(user))
    with pytest.raises(PermissionError):
        controls.register(user, "SA-1", Session(actor(), ticket_id="SA-another"))


def test_revoked_registration_is_removed_on_status_query():
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    controls.register(user, "SA-1", session)
    session.live = False
    assert controls.status(user, "SA-1")["connected"] is False
    assert session.stopped


def test_web_route_contract_no_start_and_strict_stop():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    server = (root / "src/leshy/web_server_impl14.py").read_text(encoding="utf-8")
    panel = (root / "web/superagent-dashboard.js").read_text(encoding="utf-8")
    assert 'parsed.path == "/v1/superagent/rdp/live"' in server
    assert 'path == "/v1/superagent/rdp/live/stop"' in server
    assert "authorized_remote_context(" in server
    assert 'payload.get("confirm") is not True' in server
    assert "Настоящий RDP кадр доступен." in panel
    assert "Управление мышью/клавиатурой: отключено." in panel
    assert "НЕ выполнено" in panel
    assert "window.confirm(" in panel


def test_superagent_registry_initialization_does_not_require_llm_lock(monkeypatch):
    from leshy.superagent import SuperAgentService
    import leshy.superagent_rdp_live_registry as controls

    # The internal factory is also used by small service mocks without the
    # inference lock: creating UI controls must never acquire the model lock.
    service = Service()
    first = SuperAgentService._rdp_controls(service)
    second = SuperAgentService._rdp_controls(service)
    assert isinstance(first, controls.RDPLiveRegistry)
    assert first is second


def test_real_factory_does_not_create_unverified_view_on_registration_failure(monkeypatch):
    from leshy.superagent import SuperAgentService
    from leshy.superagent_rdp_cognitive_runtime import SuperAgentRDPCognitiveSession
    import leshy.superagent_rdp_cognitive_runtime as runtime

    # This test does not invoke a real transport or create Web approval.
    # Session constructor is mocked solely to cover cleanup on registry denial.
    class NewSession:
        stopped = False
        def stop(self):
            self.stopped = True
    new = NewSession()
    monkeypatch.setattr(runtime, "SuperAgentRDPCognitiveSession", lambda *a, **kw: new)

    class Registry:
        def register(self, *args):
            raise PermissionError("registry rejected")
    instance = object.__new__(SuperAgentService)
    instance._rdp_controls = lambda: Registry()
    with pytest.raises(PermissionError):
        instance.create_rdp_cognitive_session(
            "SA-1", actor=actor(), node_id="10.0.0.5",
            observed_fingerprint="pin",
        )
    assert new.stopped


def test_registered_but_no_transport_does_not_claim_a_live_rdp_connection():
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    session.snapshot = lambda: {
        "graphics_active": False, "node_id": "10.0.0.5",
        "ticket_id": "SA-1", "stopped": False,
    }
    controls.register(user, "SA-1", session)
    data = controls.status(user, "SA-1")
    assert data["registered"] is True
    assert data["connected"] is False
    assert data["screen_available"] is False
    assert data["status"] == "waiting_for_native_transport"
    assert controls.stop(user, "SA-1")["stopped"] is True


def test_remote_admin_toggle_is_explicit_and_off_by_default():
    from leshy.module_settings import MODULE_SETTINGS
    options = {item.name: item for item in MODULE_SETTINGS}
    assert "remote-admin" in options
    assert options["remote-admin"].default_enabled is False
    assert "superagent" in options["remote-admin"].dependencies



def test_no_replacement_registration_while_previous_stop_is_in_flight():
    import threading
    controls = RDPLiveRegistry(Service())
    user = actor()
    original = Session(user)
    entered, release = threading.Event(), threading.Event()

    def slow_stop():
        entered.set()
        assert release.wait(2)
        original.stopped = True

    original.stop = slow_stop
    controls.register(user, "SA-1", original)
    done = []
    worker = threading.Thread(target=lambda: done.append(controls.stop(user, "SA-1")))
    worker.start()
    assert entered.wait(2)
    with pytest.raises(RuntimeError, match="One live RDP"):
        controls.register(user, "SA-1", Session(user))
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert done[0]["stopped"] is True
    replacement = Session(user)
    controls.register(user, "SA-1", replacement)
    assert controls.status(user, "SA-1")["registered"] is True


def test_failed_stop_fails_closed_for_same_task():
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    def fail_stop():
        raise OSError("socket in uncertain state")
    session.stop = fail_stop
    controls.register(user, "SA-1", session)
    with pytest.raises(OSError):
        controls.stop(user, "SA-1")
    with pytest.raises(RuntimeError):
        controls.register(user, "SA-1", Session(user))


def test_stop_ticket_serializes_with_new_registration():
    import threading
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    entered, release = threading.Event(), threading.Event()
    def slow_stop():
        entered.set()
        assert release.wait(2)
        session.stopped = True
    session.stop = slow_stop
    controls.register(user, "SA-1", session)
    thread = threading.Thread(target=lambda: controls.stop_ticket("alice", "SA-1"))
    thread.start()
    assert entered.wait(2)
    with pytest.raises(RuntimeError):
        controls.register(user, "SA-1", Session(user))
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()



def test_stop_all_attempts_every_session_when_first_channel_close_fails():
    class MultipleOwnerService(Service):
        def get(self, ticket_id, *, owner_id):
            if owner_id != "alice" or ticket_id not in {"SA-1", "SA-2"}:
                raise PermissionError("foreign ticket")
            return {"state": "working"}
    controls = RDPLiveRegistry(MultipleOwnerService())
    user = actor()
    first, second = Session(user, "SA-1"), Session(user, "SA-2")
    def fail_close():
        raise OSError("first socket failed")
    first.stop = fail_close
    controls.register(user, "SA-1", first)
    controls.register(user, "SA-2", second)
    with pytest.raises(OSError):
        controls.stop_all()
    assert second.stopped
    with pytest.raises(RuntimeError):
        controls.register(user, "SA-1", Session(user, "SA-1"))
    controls.register(user, "SA-2", Session(user, "SA-2"))



def test_status_discloses_stop_in_progress_not_absent_session():
    import threading
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    entered, release = threading.Event(), threading.Event()
    def slow_stop():
        entered.set()
        assert release.wait(2)
        session.stopped = True
    session.stop = slow_stop
    controls.register(user, "SA-1", session)
    thread = threading.Thread(target=lambda: controls.stop(user, "SA-1"))
    thread.start()
    assert entered.wait(2)
    status = controls.status(user, "SA-1")
    assert status["registered"] and not status["connected"]
    assert status["status"] == "stop_in_progress"
    assert not status["screen_available"]
    assert controls.stop(user, "SA-1")["status"] == "stop_in_progress"
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not controls.status(user, "SA-1")["registered"]


def test_failed_tls_close_reports_locked_and_never_allows_reconnect():
    controls = RDPLiveRegistry(Service())
    user = actor()
    session = Session(user)
    session.stop = lambda: (_ for _ in ()).throw(OSError("TLS close failed"))
    controls.register(user, "SA-1", session)
    with pytest.raises(OSError):
        controls.stop(user, "SA-1")
    status = controls.status(user, "SA-1")
    assert status["status"] == "stop_failed_locked"
    assert status["registered"] and not status["connected"]
    assert not status["screen_available"]
    assert controls.stop(user, "SA-1")["status"] == "stop_failed_locked"
    with pytest.raises(RuntimeError):
        controls.register(user, "SA-1", Session(user))



def test_web_rdp_stop_uses_durable_owner_ticket_transition():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = (root / "src/leshy/web_server_impl14.py").read_text(encoding="utf-8")
    ui = (root / "web/superagent-dashboard.js").read_text(encoding="utf-8")
    start = code.index('if path == "/v1/superagent/rdp/live/stop"')
    end = code.index('if path in {', start)
    route = code[start:end]
    assert "service.stop_ticket(" in route
    assert "service.get(" in route
    assert "_rdp_explicit_task(" in route
    assert "ticket_stopped" in route
    assert "service.rdp_live_stop(" not in route
    assert "окончательно закрыть это задание" in ui
    assert "Для нового входа потребуется новое" in ui


def test_stop_requires_correct_own_rdp_ticket_not_an_unrelated_ssh_task():
    from pathlib import Path
    code = (Path(__file__).resolve().parents[1] /
            "src/leshy/web_server_impl14.py").read_text(encoding="utf-8")
    route = code.split('if path == "/v1/superagent/rdp/live/stop":', 1)[1]
    route = route.split('if path in {', 1)[0]
    assert 'if not _rdp_explicit_task(str(ticket.get("request_text") or ""))' in route
    assert 'service.stop_ticket(' in route
