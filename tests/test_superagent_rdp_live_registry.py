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
    assert record["connected"] is False
    assert not record["screen_available"] and not record["input_enabled"]
    assert record["proposal"] is None


def test_register_live_session_reports_read_only_state():
    service = Service()
    controls = RDPLiveRegistry(service)
    user = actor()
    session = Session(user)
    controls.register(user, "SA-1", session)
    record = controls.status(user, "SA-1")
    assert record["connected"] is True and record["graphics_active"] is True
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
    assert "Изображение в Web: недоступно" in panel
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
