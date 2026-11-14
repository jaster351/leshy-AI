"""Isolated X.224 / TLS tests: no network, credentials or RDP server needed."""
from __future__ import annotations

import hashlib
import struct
from unittest.mock import patch

import pytest

from leshy.rdp_tls_preflight import (
    RDPPreflightError,
    _connection_request,
    _parse_connection_confirm,
    probe_rdp_tls,
)


def server_reply(protocol: int = 2, *, kind: int = 2) -> bytes:
    negotiation = struct.pack("<BBHI", kind, 0, 8, protocol)
    x224 = bytes((14, 0xD0, 0, 0, 0, 0, 0)) + negotiation
    return struct.pack(">BBH", 3, 0, 4 + len(x224)) + x224


class FakeSocket:
    def __init__(self, payload: bytes):
        self.pending = payload
        self.sent = b""
        self.closed = False
        self.timeout = None

    def recv(self, amount):
        # Model fragmentation as in a real TCP stream.
        chunk = self.pending[:min(amount, 3)]
        self.pending = self.pending[len(chunk):]
        return chunk

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent += data

    def close(self):
        self.closed = True


class FakeTLS:
    def __init__(self, cert=b"mock-der-remote-cert"):
        self.cert = cert
        self.closed = False

    def getpeercert(self, *, binary_form):
        assert binary_form is True
        return self.cert

    def version(self):
        return "TLSv1.3"

    def close(self):
        self.closed = True


def test_x224_request_is_microsoft_wire_format():
    request = _connection_request()
    assert len(request) == 19
    assert request[:4] == b"\x03\x00\x00\x13"
    assert request[4:11] == b"\x0e\xe0\x00\x00\x00\x00\x00"
    assert request[11:15] == b"\x01\x00\x08\x00"
    assert struct.unpack("<I", request[-4:])[0] == 0x0B


@pytest.mark.parametrize("protocol,label", [(1, "tls"), (2, "credssp"), (8, "credssp_early_auth")])
def test_secure_rdp_confirm(protocol, label):
    assert _parse_connection_confirm(server_reply(protocol)) == label


@pytest.mark.parametrize("packet", [
    server_reply(0),
    server_reply(4),
    server_reply(16),
    server_reply(3, kind=3),
    b"",
    server_reply(2)[:-1],
    b"wrong",
])
def test_downgrade_and_malformed_rdp_rejected(packet):
    with pytest.raises(RDPPreflightError):
        _parse_connection_confirm(packet)


def test_pinned_tls_negotiation_without_authentication():
    raw = FakeSocket(server_reply(8))
    tls = FakeTLS()
    calls = []
    pin = hashlib.sha256(tls.cert).hexdigest()
    with patch("leshy.rdp_tls_preflight.socket.create_connection", return_value=raw) as dial:
        with patch("leshy.rdp_tls_preflight.ssl.SSLContext.wrap_socket", return_value=tls):
            result = probe_rdp_tls("192.0.2.100", pin, authorize=lambda ip: calls.append(ip))
    dial.assert_called_once_with(("192.0.2.100", 3389), timeout=5.0)
    assert calls == ["192.0.2.100", "192.0.2.100"]
    assert raw.sent == _connection_request()
    assert tls.closed is True
    assert result.security_protocol == "credssp_early_auth"
    assert result.tls_certificate_verified is True
    assert result.authenticated_desktop is False


def test_certificate_mismatch_aborts_before_post_auth():
    raw, tls = FakeSocket(server_reply(2)), FakeTLS()
    calls = []
    with patch("leshy.rdp_tls_preflight.socket.create_connection", return_value=raw):
        with patch("leshy.rdp_tls_preflight.ssl.SSLContext.wrap_socket", return_value=tls):
            with pytest.raises(PermissionError, match="identity"):
                probe_rdp_tls("192.0.2.100", "a" * 64, authorize=lambda ip: calls.append(ip))
    assert calls == ["192.0.2.100"]
    assert tls.closed is True


def test_revocation_during_tls_aborts_and_closes():
    raw, tls = FakeSocket(server_reply(2)), FakeTLS()
    calls = []
    def authorize(ip):
        calls.append(ip)
        if len(calls) == 2:
            raise PermissionError("revoked")
    with patch("leshy.rdp_tls_preflight.socket.create_connection", return_value=raw):
        with patch("leshy.rdp_tls_preflight.ssl.SSLContext.wrap_socket", return_value=tls):
            with pytest.raises(PermissionError, match="revoked"):
                probe_rdp_tls("192.0.2.100", hashlib.sha256(tls.cert).hexdigest(), authorize=authorize)
    assert tls.closed is True


def test_unsafe_targets_and_missing_pins_do_not_dial():
    with patch("leshy.rdp_tls_preflight.socket.create_connection") as dial:
        with pytest.raises(ValueError):
            probe_rdp_tls("not-a-literal-address.example", "a" * 64, authorize=lambda _: None)
        with pytest.raises(PermissionError):
            probe_rdp_tls("192.0.2.100", "", authorize=lambda _: None)
        with pytest.raises(PermissionError):
            probe_rdp_tls("192.0.2.100", "a" * 64, authorize=None)
        with pytest.raises(ValueError):
            probe_rdp_tls("192.0.2.100", "a" * 64, authorize=lambda _: None, timeout=99)
        dial.assert_not_called()


def test_reject_legacy_rdp_without_tls():
    raw = FakeSocket(server_reply(0))
    with patch("leshy.rdp_tls_preflight.socket.create_connection", return_value=raw):
        with patch("leshy.rdp_tls_preflight.ssl.SSLContext.wrap_socket") as wrap:
            with pytest.raises(RDPPreflightError):
                probe_rdp_tls("192.0.2.100", "a" * 64, authorize=lambda _: None)
            wrap.assert_not_called()
    assert raw.closed


def test_total_timeout_aborts_before_network_io():
    raw = FakeSocket(server_reply(2))
    with patch("leshy.rdp_tls_preflight.socket.create_connection", return_value=raw):
        with patch("leshy.rdp_tls_preflight.time.monotonic", side_effect=[0.0, 2.0]):
            with pytest.raises(RDPPreflightError, match="overall timeout"):
                probe_rdp_tls("192.0.2.100", "a" * 64, authorize=lambda _: None, timeout=1)
    assert raw.sent == b""
    assert raw.closed
