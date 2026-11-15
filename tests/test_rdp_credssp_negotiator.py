"""CredSSP flow tests with fake GSS/SSPI: no credentials or network used."""
from __future__ import annotations

import pytest
import leshy.rdp_credssp_negotiator as protocol
from leshy.rdp_credssp_tsrequest import TSRequest


class FakeMechanism:
    def __init__(self):
        self.complete = False
        self.closed = False
        self.steps = []
        self.server_hash = b"s" * 32

    def step(self, input=None):
        self.steps.append(input)
        if input is not None:
            self.complete = True
        return b"GSS-step-" + str(len(self.steps)).encode()

    def wrap(self, plain):
        assert self.complete
        return b"sealed:" + plain

    def unwrap(self, ciphertext):
        assert ciphertext == b"server-proof"
        return self.server_hash

    def close(self):
        self.closed = True


class FakeBinding:
    nonce = b"n" * 32
    client_hash = b"c" * 32
    server_hash = b"s" * 32

    def verify_unsealed_server_hash(self, value):
        if value != self.server_hash:
            raise PermissionError("TLS binding mismatch")


@pytest.fixture
def mocked_codec(monkeypatch):
    # Actual DER codec has its own independent test suite. Here isolate states.
    monkeypatch.setattr(protocol, "prepare_credssp_binding", lambda der, pin: FakeBinding()
                        if pin == "enrolled-pin" else (_ for _ in ()).throw(PermissionError("pin")))
    monkeypatch.setattr(protocol, "encode_tsrequest", lambda value: value)
    monkeypatch.setattr(protocol, "decode_tsrequest", lambda value: value)


def new_client():
    mechanism = FakeMechanism()
    client = protocol.CredSSPClient(mechanism, b"certificate", "enrolled-pin",
                                   authorize=lambda: None)
    return client, mechanism


def test_spnego_negotiation_public_key_binding_no_password(mocked_codec):
    client, mechanism = new_client()
    initial = client.start()
    assert initial.nego_tokens == (b"GSS-step-1",)
    assert initial.pub_key_auth is None
    next_packet = client.receive(TSRequest(nego_tokens=(b"server-token",)))
    assert next_packet.nego_tokens == (b"GSS-step-2",)
    assert next_packet.pub_key_auth == b"sealed:" + b"c" * 32
    assert next_packet.client_nonce == b"n" * 32
    assert not client.server_bound
    assert client.receive(TSRequest(pub_key_auth=b"server-proof")) is None
    assert client.server_bound
    assert not client.credentials_delegated
    client.close()
    assert mechanism.closed


@pytest.mark.parametrize("bad", (
    TSRequest(error_code=0xC000006D, nego_tokens=(b"token",)),
    TSRequest(pub_key_auth=b"premature"),
    TSRequest(nego_tokens=()),
    TSRequest(nego_tokens=(b"one", b"two")),
    TSRequest(nego_tokens=(b"token",), client_nonce=b"x" * 32),
))
def test_illegal_transition_closes_mechanism(mocked_codec, bad):
    client, mechanism = new_client()
    client.start()
    with pytest.raises((protocol.AuthenticationError, PermissionError, ValueError)):
        client.receive(bad)
    assert mechanism.closed
    assert not client.server_bound


def test_wrong_binding_is_not_authenticated(mocked_codec):
    client, mechanism = new_client()
    client.start()
    client.receive(TSRequest(nego_tokens=(b"server",)))
    mechanism.server_hash = b"wrong"
    with pytest.raises(PermissionError):
        client.receive(TSRequest(pub_key_auth=b"server-proof"))
    assert mechanism.closed
    assert not client.server_bound


def test_owner_revocation_aborts_handshake(mocked_codec):
    allowed = [True]
    mechanism = FakeMechanism()

    def authorize():
        if not allowed[0]:
            raise PermissionError("revoked")

    client = protocol.CredSSPClient(mechanism, b"certificate", "enrolled-pin", authorize=authorize)
    client.start()
    allowed[0] = False
    with pytest.raises(PermissionError):
        client.receive(TSRequest(nego_tokens=(b"server",)))
    assert mechanism.closed


def test_repeated_start_and_closed_context_denied(mocked_codec):
    client, _ = new_client()
    client.start()
    with pytest.raises(protocol.AuthenticationError):
        client.start()
    client.close()
    with pytest.raises(protocol.AuthenticationError):
        client.receive(TSRequest(nego_tokens=(b"x",)))
