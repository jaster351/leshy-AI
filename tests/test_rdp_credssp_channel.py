"""Credential-free pinned RDP TLS channel and DER framing tests."""
from __future__ import annotations

import hashlib
import socket
import ssl
import time
from unittest.mock import patch

import pytest

import leshy.rdp_credssp_channel as chan
from leshy.rdp_credssp_tsrequest import TSRequest, decode_tsrequest, encode_tsrequest


class Stream:
    def __init__(self, data=b"", cert=b"test-pinned-certificate"):
        self.pending = bytearray(data)
        self.sent = []
        self.closed = False
        self.cert = cert
        self.timeout = 0

    def recv(self, n):
        received = bytes(self.pending[:min(n, 2)])
        del self.pending[:len(received)]
        return received

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, timeout):
        self.timeout = timeout
        if timeout <= 0:
            raise AssertionError("Deadline must never permit negative timeout")

    def getpeercert(self, *, binary_form):
        assert binary_form
        return self.cert

    def version(self):
        return "TLSv1.3"

    def close(self):
        self.closed = True


class Handshake:
    def __init__(self, mechanism, cert, pin, *, authorize):
        authorize()
        self.closed = False
        self.server_bound = False
        self.steps = 0

    def start(self):
        return encode_tsrequest(TSRequest(nego_tokens=(b"client-gss",)))

    def receive(self, packet):
        req = decode_tsrequest(packet)
        self.steps += 1
        if self.steps == 1:
            assert req.nego_tokens == (b"server-gss",)
            return encode_tsrequest(TSRequest(
                pub_key_auth=b"sealed-binding", client_nonce=b"n" * 32
            ))
        if self.steps == 2:
            assert req.pub_key_auth == b"server-binding"
            self.server_bound = True
            return None
        raise AssertionError("unexpected GSS exchange")

    def close(self):
        self.closed = True


@pytest.fixture
def messages():
    return (
        encode_tsrequest(TSRequest(nego_tokens=(b"server-gss",))) +
        encode_tsrequest(TSRequest(pub_key_auth=b"server-binding"))
    )


def test_fragmented_der_framing_reads_exactly_one_message(messages):
    stream = Stream(messages)
    one = chan.read_tsrequest(stream, deadline=time.monotonic() + 5)
    assert decode_tsrequest(one).nego_tokens == (b"server-gss",)
    two = chan.read_tsrequest(stream, deadline=time.monotonic() + 5)
    assert decode_tsrequest(two).pub_key_auth == b"server-binding"
    assert stream.pending == b""


@pytest.mark.parametrize("payload", (
    b"", b"\x31\x00", b"\x30\x80",
    b"\x30\x81\x05\xa0\x03\x02\x01\x06",
    b"\x30\x82\x00\x80", b"\x30\x84\x00\x04\x00\x00",
    b"\x30\x82\xff\xff", b"\x30\x00",
))
def test_malformed_der_fails_without_unbounded_read(payload):
    stream = Stream(payload)
    with pytest.raises((chan.RDPChannelError, ValueError)):
        chan.read_tsrequest(stream, deadline=time.monotonic() + 2)
    assert len(stream.pending) <= len(payload)


def test_live_channel_pinned_tls_and_real_framing(messages):
    cert = b"test-pinned-certificate"
    pin = hashlib.sha256(cert).hexdigest()
    tls, raw = Stream(messages, cert=cert), Stream()
    calls = []

    def authorize(ip, dns):
        calls.append((ip, dns))

    with patch.object(chan.socket, "create_connection", return_value=raw), \
            patch.object(chan, "_negotiate", return_value="CredSSP"), \
            patch.object(chan.ssl.SSLContext, "wrap_socket", return_value=tls), \
            patch.object(chan, "CredSSPClient", Handshake):
        with chan.connect_verified_rdp_credssp(
            "192.0.2.2", "rdp.example.org", pin,
            authorize=authorize, timeout=10
        ) as session:
            assert not tls.closed
            assert not session.server_bound
            status = session.authenticate_server(mechanism_factory=lambda *a, **kw: object())
            assert session.server_bound
            assert status.server_bound and status.pinned_certificate
            assert not status.rdp_desktop_available
            assert not status.credentials_delegated
            assert len(tls.sent) == 2
            assert decode_tsrequest(tls.sent[0]).nego_tokens == (b"client-gss",)
            assert decode_tsrequest(tls.sent[1]).pub_key_auth == b"sealed-binding"
            assert all(item == ("192.0.2.2", "rdp.example.org") for item in calls)
            assert len(calls) >= 5
        assert tls.closed


def test_wrong_tls_certificate_fails_before_native_spnego():
    tls, raw = Stream(cert=b"impostor"), Stream()
    with patch.object(chan.socket, "create_connection", return_value=raw), \
            patch.object(chan, "_negotiate", return_value="CredSSP"), \
            patch.object(chan.ssl.SSLContext, "wrap_socket", return_value=tls), \
            patch.object(chan, "CredSSPClient") as auth:
        with pytest.raises(PermissionError, match="fingerprint"):
            chan.connect_verified_rdp_credssp(
                "192.0.2.2", "rdp.example.org", "a" * 64, authorize=lambda *_: None
            )
        auth.assert_not_called()
        assert tls.closed


def test_tls_only_protocol_rejected_and_raw_socket_closed():
    raw = Stream()
    with patch.object(chan.socket, "create_connection", return_value=raw), \
            patch.object(chan, "_negotiate", return_value="TLS"), \
            patch.object(chan.ssl.SSLContext, "wrap_socket") as wrap:
        with pytest.raises(chan.RDPChannelError, match="CredSSP"):
            chan.connect_verified_rdp_credssp(
                "192.0.2.2", "rdp.example.org", "a" * 64, authorize=lambda *_: None
            )
        wrap.assert_not_called()
        assert raw.closed


def test_revoked_owner_blocks_network_before_socket():
    with patch.object(chan.socket, "create_connection") as dial:
        with pytest.raises(PermissionError):
            chan.connect_verified_rdp_credssp(
                "192.0.2.2", "rdp.example.org", "a" * 64,
                authorize=lambda *_: (_ for _ in ()).throw(PermissionError("revoked"))
            )
        dial.assert_not_called()


@pytest.mark.parametrize("ip,dns,pin,timeout,port", (
    ("rdp.example.org", "rdp.example.org", "a" * 64, 5, 3389),
    ("192.0.2.2", "192.0.2.2", "a" * 64, 5, 3389),
    ("192.0.2.2", "rdp.example.org", "badpin", 5, 3389),
    ("192.0.2.2", "rdp.example.org", "a" * 64, True, 3389),
    ("192.0.2.2", "rdp.example.org", "a" * 64, 5, 0),
))
def test_invalid_target_or_limits_rejected_before_network(ip, dns, pin, timeout, port):
    with patch.object(chan.socket, "create_connection") as dial:
        with pytest.raises((ValueError, PermissionError)):
            chan.connect_verified_rdp_credssp(
                ip, dns, pin, timeout=timeout, port=port, authorize=lambda *_: None
            )
        dial.assert_not_called()


def test_owner_revoked_during_credssp_closes_channel(messages):
    tls, raw = Stream(messages), Stream()
    pin = hashlib.sha256(tls.cert).hexdigest()
    permitted = [True]
    def authorize(*_):
        if not permitted[0]:
            raise PermissionError("revoked")

    with patch.object(chan.socket, "create_connection", return_value=raw), \
            patch.object(chan, "_negotiate", return_value="CredSSP"), \
            patch.object(chan.ssl.SSLContext, "wrap_socket", return_value=tls), \
            patch.object(chan, "CredSSPClient", Handshake):
        conn = chan.connect_verified_rdp_credssp(
            "192.0.2.2", "rdp.example.org", pin, authorize=authorize
        )
        permitted[0] = False
        with pytest.raises(PermissionError, match="revoked"):
            conn.authenticate_server(mechanism_factory=lambda *a, **kw: object())
        assert tls.closed


def test_long_form_der_length_and_tls_fragmentation():
    packet = encode_tsrequest(TSRequest(nego_tokens=(b"x" * 200,)))
    assert packet[1] & 0x80
    stream = Stream(packet)
    assert chan.read_tsrequest(stream, deadline=time.monotonic() + 5) == packet
    assert not stream.pending


def test_credential_bearing_tsrequest_is_never_accepted():
    # A valid version plus explicit [2] authInfo field. Even though the
    # enclosing length is correct, this transport has no credential path.
    packet = bytes.fromhex("3009a003020106a2020400")
    with pytest.raises(ValueError, match="Credential delegation"):
        chan.read_tsrequest(Stream(packet), deadline=time.monotonic() + 5)



@pytest.mark.parametrize("bad_timeout", [
    float("nan"), float("inf"), float("-inf"), True, False,
    None, "10", 0, -1, 31, 100000000,
])
def test_native_rdp_rejects_bad_timeout_before_owner_or_tcp(bad_timeout):
    approvals = []
    with patch.object(chan.socket, "create_connection") as connect:
        with pytest.raises(ValueError, match="timeout"):
            chan.connect_verified_rdp_credssp(
                "192.0.2.2", "rdp.example.org", "a" * 64,
                authorize=lambda *a: approvals.append(a),
                timeout=bad_timeout,
            )
        connect.assert_not_called()
    assert approvals == []


@pytest.mark.parametrize("bad_address", [
    3221225986, True, False, None, b"192.0.2.2", "",
    "192.0.2.2\r\n", "999.999.999.999", "host.example.org",
    "192.0.2.2" * 20,
])
def test_native_rdp_requires_exact_literal_ip_not_alias(bad_address):
    approvals = []
    with patch.object(chan.socket, "create_connection") as connect:
        with pytest.raises(ValueError, match="IP"):
            chan.connect_verified_rdp_credssp(
                bad_address, "rdp.example.org", "a" * 64,
                authorize=lambda *a: approvals.append(a),
            )
        connect.assert_not_called()
    assert approvals == []


@pytest.mark.parametrize("invalid_port", [
    None, True, False, "3389", 0, -1, 65536, 99999, 3389.0,
])
def test_native_rdp_rejects_invalid_port_without_network(invalid_port):
    with patch.object(chan.socket, "create_connection") as connect:
        with pytest.raises(ValueError, match="port"):
            chan.connect_verified_rdp_credssp(
                "192.0.2.2", "rdp.example.org", "a" * 64,
                authorize=lambda *a: None, port=invalid_port,
            )
        connect.assert_not_called()


@pytest.mark.parametrize("bad_pin", [
    None, True, b"a" * 64, "", "a" * 63, "a" * 65, "z" * 64,
])
def test_native_rdp_rejects_unenrolled_certificate_pin(bad_pin):
    with patch.object(chan.socket, "create_connection") as connect:
        with pytest.raises(PermissionError):
            chan.connect_verified_rdp_credssp(
                "192.0.2.2", "rdp.example.org", bad_pin,
                authorize=lambda *a: None,
            )
        connect.assert_not_called()


def test_native_rdp_owner_denial_happens_before_tcp_connect():
    with patch.object(chan.socket, "create_connection") as connect:
        with pytest.raises(PermissionError):
            chan.connect_verified_rdp_credssp(
                "192.0.2.2", "rdp.example.org", "a" * 64,
                authorize=lambda *a: (_ for _ in ()).throw(
                    PermissionError("revoked owner")
                ),
            )
        connect.assert_not_called()
