"""X.224/TLS preflight tests; a local mock server is used, never a real host."""
from __future__ import annotations

import hashlib
import shutil
import socket
import ssl
import struct
import subprocess
import threading

import pytest

from leshy.rdp_tls_probe import _negotiate, probe_rdp_tls


def response(value=2, kind=2):
    return (
        b"\x03\x00\x00\x13\x0e\xd0\x00\x00\x00\x00\x00" +
        struct.pack("<BBHI", kind, 0, 8, value)
    )


class FragmentingSocket:
    def __init__(self, payload):
        self.payload = bytearray(payload)
        self.sent = None

    def sendall(self, content):
        self.sent = content

    def recv(self, length):
        actual = min(2, length, len(self.payload))
        value = bytes(self.payload[:actual])
        del self.payload[:actual]
        return value


@pytest.mark.parametrize("value,expected", [(1, "TLS"), (2, "CredSSP"), (8, "CredSSP-EX")])
def test_nego_valid(value, expected):
    fake = FragmentingSocket(response(value))
    assert _negotiate(fake) == expected
    assert fake.sent.startswith(b"\x03\x00\x00\x13")
    assert struct.unpack("<I", fake.sent[-4:])[0] == 11


@pytest.mark.parametrize("payload", [
    response(0), response(4), response(1, 3),
    b"\x03\x00\x00\x13" + b"\x00" * 15, b"\x03\x00\x00\x02",
])
def test_nego_rejects_downgrade_and_invalid(payload):
    with pytest.raises((ConnectionError, ValueError)):
        _negotiate(FragmentingSocket(payload))


@pytest.mark.parametrize("host,pin,port,timeout", [
    ("not-a-host", "a" * 64, 3389, 1),
    ("127.0.0.1", "invalid", 3389, 1),
    ("127.0.0.1", "a" * 64, 0, 1),
    ("127.0.0.1", "a" * 64, 3389, 999),
])
def test_invalid_input_no_connection(host, pin, port, timeout, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("unexpected network"))
    with pytest.raises((ValueError, PermissionError)):
        probe_rdp_tls(host, pin, port=port, timeout=timeout)


@pytest.fixture
def local_tls_cert(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("OpenSSL test utility not available")
    crt, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-sha256", "-nodes", "-days", "1", "-subj", "/CN=localhost",
        "-keyout", str(key), "-out", str(crt),
    ], capture_output=True, check=True)
    der = ssl.PEM_cert_to_DER_cert(crt.read_text())
    return crt, key, hashlib.sha256(der).hexdigest()


def local_rdp_server(crt, key, selection=2):
    ready = threading.Event()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    errors = []

    def serve():
        try:
            ready.set()
            with listener:
                client, _ = listener.accept()
                with client:
                    client.settimeout(5)
                    msg = client.recv(1024)
                    if not msg.startswith(b"\x03\x00\x00\x13"):
                        return
                    client.sendall(response(selection))
                    if selection in (1, 2, 8):
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        context.load_cert_chain(str(crt), str(key))
                        with context.wrap_socket(client, server_side=True):
                            pass
        except (ssl.SSLError, ConnectionResetError, OSError) as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    ready.wait(1)
    return port, worker, errors


def test_real_local_socket_tls_pin_roundtrip(local_tls_cert):
    crt, key, pin = local_tls_cert
    port, worker, _ = local_rdp_server(crt, key)
    result = probe_rdp_tls("127.0.0.1", pin, port=port)
    worker.join(3)
    assert result.selected_protocol == "CredSSP"
    assert result.certificate_sha256 == pin
    assert result.tls_version in ("TLSv1.2", "TLSv1.3")
    assert result.server_tls_pinned
    assert not result.rdp_login_complete
    assert not result.rdp_desktop_available


def test_real_local_socket_rejects_wrong_pin(local_tls_cert):
    crt, key, pin = local_tls_cert
    port, worker, _ = local_rdp_server(crt, key)
    wrong = "f" * 64 if pin != "f" * 64 else "a" * 64
    with pytest.raises(PermissionError, match="fingerprint mismatch"):
        probe_rdp_tls("127.0.0.1", wrong, port=port)
    worker.join(3)


def test_real_local_socket_rejects_legacy_rdp(local_tls_cert):
    crt, key, pin = local_tls_cert
    port, worker, _ = local_rdp_server(crt, key, selection=0)
    with pytest.raises(ConnectionError, match="supported TLS"):
        probe_rdp_tls("127.0.0.1", pin, port=port)
    worker.join(3)


def test_rejects_nonzero_x224_class_options():
    packet = bytearray(response(2))
    packet[10] = 1
    with pytest.raises(ConnectionError, match="X.224"):
        _negotiate(FragmentingSocket(bytes(packet)))


def test_boolean_timeout_rejected_before_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("unexpected network"))
    with pytest.raises(ValueError, match="timeout"):
        probe_rdp_tls("127.0.0.1", "a" * 64, timeout=True)


def test_slow_drip_exceeds_total_handshake_deadline():
    from unittest.mock import patch
    fake = FragmentingSocket(response(2))
    fake.settimeout = lambda _: None
    with patch("leshy.rdp_tls_probe.time.monotonic", side_effect=[0.5, 0.6, 1.1]):
        with pytest.raises(TimeoutError, match="total timeout"):
            _negotiate(fake, deadline=1.0)
    assert fake.sent is not None
    assert len(fake.payload) == len(response(2)) - 2
