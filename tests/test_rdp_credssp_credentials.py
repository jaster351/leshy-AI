"""CredSSP credentials: DER correctness, security gates and no remote login claim.

All tests are in-process; no secret files, real passwords or RDP connections.
"""
from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from leshy.rdp_credssp_credentials import (
    RDPCredentials, password_credentials_der, sealed_authinfo_tsrequest,
    validate_sealed_authinfo_tsrequest,
)
from leshy.rdp_credssp_tsrequest import (
    TSRequestError, decode_tsrequest, _one, _read, _number,
)
from leshy.rdp_credssp_negotiator import (
    AuthenticationError, CredSSPClient,
)
from leshy.rdp_credssp_channel import (
    PinnedRDPCredSSPChannel, RDPChannelError,
)


def _field(data: bytes, expected: int, at: int):
    tag, inner, pos = _read(data, at, expected)
    return inner, pos


def test_password_credentials_der_has_correct_mscssp_wrappers():
    creds = RDPCredentials("Алиса", "safe-example", "DOMAIN")
    encoded = password_credentials_der(creds)
    outer = _one(encoded, 0x30)
    ctype, offset = _field(outer, 0xA0, 0)
    assert _number(ctype, 5) == 1
    payload, offset = _field(outer, 0xA1, offset)
    assert offset == len(outer)
    inner = _one(_one(payload, 0x04), 0x30)
    expected = ("DOMAIN", "Алиса", "safe-example")
    for index, text in enumerate(expected):
        wrapper, offset = _field(inner, 0xA0 + index, 0 if index == 0 else offset)
        assert _one(wrapper, 0x04).decode("utf-16-le") == text
    assert offset == len(inner)
    assert "safe-example" not in repr(creds)
    assert password_credentials_der(RDPCredentials("alice", "ok", ""))


@pytest.mark.parametrize("credentials", [
    RDPCredentials("", "password"),
    RDPCredentials("alice", ""),
    RDPCredentials("alice\nroot", "password"),
    RDPCredentials("alice", "line\rpassword"),
    RDPCredentials("alice", "a" * 3000),
    RDPCredentials("alice", "password", "\ud800"),
])
def test_invalid_credentials_rejected_before_wrap(credentials):
    with pytest.raises((ValueError, TypeError)):
        password_credentials_der(credentials)


def test_sealed_authinfo_is_outbound_only_and_strictly_framed():
    sealed = b"fake-sealed-gss-output"
    packet = sealed_authinfo_tsrequest(sealed)
    validate_sealed_authinfo_tsrequest(packet)
    with pytest.raises(TSRequestError, match="delegation disabled"):
        decode_tsrequest(packet)
    outer = _one(packet, 0x30)
    version, at = _field(outer, 0xA0, 0)
    assert _number(version, 6) == 6
    auth, at = _field(outer, 0xA2, at)
    assert _one(auth, 0x04) == sealed
    assert at == len(outer)
    for invalid in (
        packet + b"\0",
        packet[:-1],
        b"\x30\x00",
        packet.replace(b"fake-sealed-gss-output", b""),
    ):
        with pytest.raises(TSRequestError):
            validate_sealed_authinfo_tsrequest(invalid)
    with pytest.raises(TSRequestError):
        sealed_authinfo_tsrequest(b"")


class BoundMechanism:
    complete = True

    def __init__(self):
        self.closed = False
        self.messages = []

    def wrap(self, raw):
        self.messages.append(hashlib.sha256(raw).hexdigest())
        return b"mock-gss-sealed-token"

    def close(self):
        self.closed = True


def bound_credssp():
    # Isolate state after the independent TLS-pinned binding handshake.
    mechanism = BoundMechanism()
    with patch("leshy.rdp_credssp_negotiator.prepare_credssp_binding",
               return_value=object()):
        client = CredSSPClient(
            mechanism, b"test-cert", "a" * 64, authorize=lambda: None,
        )
    client._state = "bound"
    return client, mechanism


def test_no_delegation_before_verified_binding():
    mechanism = BoundMechanism()
    with patch("leshy.rdp_credssp_negotiator.prepare_credssp_binding",
               return_value=object()):
        client = CredSSPClient(
            mechanism, b"test-cert", "a" * 64, authorize=lambda: None,
        )
    with pytest.raises(AuthenticationError):
        client.prepare_password_delegation(
            RDPCredentials("alice", "example"),
            authorize_delegation=lambda: None,
        )
    assert not mechanism.messages


def test_delegation_requires_separate_approval_and_single_use():
    client, mechanism = bound_credssp()
    creds = RDPCredentials("alice", "example")
    with pytest.raises(PermissionError):
        client.prepare_password_delegation(
            creds, authorize_delegation=None,
        )
    assert not mechanism.messages
    approvals = []
    packet = client.prepare_password_delegation(
        creds, authorize_delegation=lambda: approvals.append(True),
    )
    assert len(approvals) == 2
    validate_sealed_authinfo_tsrequest(packet)
    assert b"example" not in packet
    assert not client.credentials_delegated
    with pytest.raises(AuthenticationError):
        client.prepare_password_delegation(creds, authorize_delegation=lambda: None)
    client.mark_password_sent()
    assert client.credentials_delegated
    with pytest.raises(AuthenticationError):
        client.mark_password_sent()
    client.close()
    assert mechanism.closed


def test_permission_revocation_aborts_before_encryption_and_closes():
    client, mechanism = bound_credssp()
    with pytest.raises(PermissionError):
        client.prepare_password_delegation(
            RDPCredentials("alice", "example"),
            authorize_delegation=lambda: (_ for _ in ()).throw(
                PermissionError("revoked")
            ),
        )
    assert not mechanism.messages
    assert mechanism.closed


class FakeTLS:
    def __init__(self):
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        assert timeout > 0

    def sendall(self, data):
        self.sent.append(data)

    def version(self):
        return "TLSv1.3"

    def close(self):
        self.closed = True


def make_channel():
    tls = FakeTLS()
    client, mechanism = bound_credssp()
    from time import monotonic
    channel = PinnedRDPCredSSPChannel(
        tls, b"cert", ip="192.0.2.10", dns_name="rdp.example.org",
        pin="a" * 64, selected_protocol="CredSSP",
        authorize=lambda ip, dns: None,
        deadline=monotonic() + 20,
    )
    channel._credssp = client
    return channel, tls


def test_channel_transmits_once_and_never_claims_desktop_ready():
    channel, tls = make_channel()
    with channel:
        status = channel.delegate_password_credentials(
            RDPCredentials("alice", "example"),
            authorize_delegation=lambda ip, dns: None,
        )
        assert status.credentials_delegated
        assert status.server_bound
        assert status.rdp_desktop_available is False
        assert len(tls.sent) == 1
        validate_sealed_authinfo_tsrequest(tls.sent[0])
        with pytest.raises(RDPChannelError):
            channel.delegate_password_credentials(
                RDPCredentials("alice", "example"),
                authorize_delegation=lambda ip, dns: None,
            )
        assert len(tls.sent) == 1
    assert tls.closed


def test_channel_rejects_missing_approval_without_sending_secrets():
    channel, tls = make_channel()
    with pytest.raises(PermissionError):
        channel.delegate_password_credentials(
            RDPCredentials("alice", "example"),
            authorize_delegation=None,
        )
    assert tls.sent == []
    channel.close()


def test_channel_closes_when_permission_revoked_during_send():
    channel, tls = make_channel()
    checks = [True]

    def approval(ip, dns):
        assert ip == "192.0.2.10" and dns == "rdp.example.org"
        checks.append(True)
        if len(checks) >= 3:
            raise PermissionError("owner revoked")

    with pytest.raises(PermissionError):
        channel.delegate_password_credentials(
            RDPCredentials("alice", "example"),
            authorize_delegation=approval,
        )
    assert tls.closed
    assert tls.sent == []
