"""Native RDP launch boundaries, mocked network only; no external server."""
from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest

from leshy.superagent_remote_permissions import VerifiedActor
import leshy.superagent_native_rdp_launcher as launch


class Service:
    def __init__(self):
        self.app = SimpleNamespace(owner_identity=object())
        self.storage = object()
        self.session = None

    def get(self, ticket_id, *, owner_id):
        if (ticket_id, owner_id) != ("SA-1", "owner"):
            raise PermissionError("not owner")
        return {"state": "working"}

    def create_rdp_cognitive_session(self, ticket_id, **args):
        self.session = Session()
        return self.session

    def _rdp_controls(self):
        return SimpleNamespace(stop_ticket=lambda *a: setattr(self.session, "stopped", True))


class Policy:
    def __init__(self, identity):
        assert identity is not None
    def require(self, actor, node, action, *, observed_fingerprint):
        assert action == "rdp"


class Session:
    def __init__(self):
        self.stopped = False
        self.phases = []
        self.fail = ""
        self.receive_count = 0

    def reauthorize(self):
        if self.stopped:
            raise PermissionError("revoked")

    def attach_native_handshake(self, connection):
        self.reauthorize()

    def set_native_phase(self, phase):
        self.reauthorize()
        self.phases.append(phase)

    def native_failure(self, failure):
        self.fail = failure

    def receive_graphics(self, connection, **kwargs):
        self.receive_count += 1

    def clear_native_handshake(self, connection):
        pass


class Channel:
    def __init__(self):
        self.closed = False
        self.events = []

    def authenticate_server(self):
        self.events.append("binding")

    def delegate_password_credentials(self, material, *, authorize_delegation):
        assert material.username == "user"
        assert material.password == "secret"
        authorize_delegation("192.0.2.10", "rdp.example.org")
        self.events.append("delegation")

    def exchange_mcs_basic_settings(self):
        self.events.append("mcs")

    def join_mcs_channels(self):
        self.events.append("join")

    def local_client_address(self):
        return "192.0.2.1"

    def send_client_info(self, *, client_address):
        assert client_address == "192.0.2.1"
        self.events.append("client_info")

    def receive_server_activation(self):
        self.events.append("activation")

    def send_confirm_active(self):
        self.events.append("confirm")

    def finalize_mcs_connection(self):
        self.events.append("finalization")

    def close(self):
        self.closed = True


@pytest.fixture
def prepared(monkeypatch):
    monkeypatch.setattr(launch, "RemotePermissionPolicy", Policy)
    monkeypatch.setattr(launch, "_registered_pin", lambda *a: "f" * 64)
    monkeypatch.setattr(launch.threading, "Thread", SynchronousThread)
    return Service()


class SynchronousThread:
    def __init__(self, *, target, **kwargs):
        self.target = target
    def start(self):
        self.target()


def start(service, *, approve):
    return launch.start_owner_authorized_native_rdp(
        service, ticket_id="SA-1",
        actor=VerifiedActor("owner", "session-pin", "web"),
        node_id="192.0.2.10", service_dns_name="rdp.example.org",
        enrolled_rdp_tls_sha256="a" * 64, credential_file="rdp.json",
        authorize_delegation=approve,
    )


def test_no_network_before_verified_owner_approval_contract(prepared, monkeypatch):
    opened = []
    monkeypatch.setattr(launch, "connect_verified_rdp_credssp",
                        lambda *a, **kw: opened.append(True))
    with pytest.raises(PermissionError):
        start(prepared, approve=None)
    assert not opened


def test_complete_real_protocol_sequence_is_delegated_to_existing_channel(prepared, monkeypatch):
    channel = Channel()
    monkeypatch.setattr(launch, "connect_verified_rdp_credssp",
                        lambda *a, **kw: channel)
    looked_up = []
    monkeypatch.setattr(launch, "_credential",
                        lambda *a: (looked_up.append(True) or ("user", "secret")))
    approvals = []
    result = start(prepared, approve=lambda ip, dns: approvals.append((ip, dns)))
    assert result["status"] == "starting"
    assert channel.events == [
        "binding", "delegation", "mcs", "join", "client_info",
        "activation", "confirm", "finalization",
    ]
    assert looked_up and len(approvals) >= 2
    assert prepared.session.receive_count == 1
    assert prepared.session.phases[-1] == "stream_finished"
    assert prepared.session.fail == "" and channel.closed


def test_revoked_credential_approval_never_reads_secret(prepared, monkeypatch):
    channel = Channel()
    monkeypatch.setattr(launch, "connect_verified_rdp_credssp",
                        lambda *a, **kw: channel)
    looked_up = []
    monkeypatch.setattr(launch, "_credential",
                        lambda *a: looked_up.append(True))
    start(prepared, approve=lambda *a: (_ for _ in ()).throw(PermissionError("revoked")))
    assert looked_up == [] and channel.events == ["binding"]
    assert prepared.session.fail == "PermissionError"
    assert channel.closed


def test_partial_failure_is_not_retried(prepared, monkeypatch):
    opened = []
    def fail_connect(*args, **kwargs):
        opened.append(1)
        raise TimeoutError("private IP and username")
    monkeypatch.setattr(launch, "connect_verified_rdp_credssp", fail_connect)
    start(prepared, approve=lambda *a: None)
    assert opened == [1]
    assert prepared.session.fail == "TimeoutError"
    assert "private" not in prepared.session.fail


@pytest.mark.parametrize("bad", [
    {"service_dns_name": "192.0.2.10"},
    {"enrolled_rdp_tls_sha256": ""},
    {"credential_file": "../x.json"},
])
def test_invalid_inputs_fail_before_connect(prepared, monkeypatch, bad):
    tries = []
    monkeypatch.setattr(launch, "connect_verified_rdp_credssp",
                        lambda *a, **kw: tries.append(1))
    kwargs = {
        "ticket_id": "SA-1", "actor": VerifiedActor("owner", "session", "web"),
        "node_id": "192.0.2.10", "service_dns_name": "rdp.example.org",
        "enrolled_rdp_tls_sha256": "b" * 64,
        "credential_file": "rdp.json", "authorize_delegation": lambda *a: None,
    }
    kwargs.update(bad)
    with pytest.raises((PermissionError, ValueError)):
        launch.start_owner_authorized_native_rdp(prepared, **kwargs)
    assert not tries
