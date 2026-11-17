"""MCS starts only after approved CredSSP handoff on the same TLS channel."""
from __future__ import annotations

import struct
import time
import pytest

from leshy import rdp_credssp_channel as ch
from leshy import rdp_mcs_basic as m


def response():
    core = struct.pack("<HHII", 0x0C01, 12, 0x00080004, 0x0B)
    network = struct.pack("<HHHH", 0x0C03, 8, 1003, 0)
    security = struct.pack("<HHII", 0x0C02, 12, 0, 0)
    blocks = core + network + security
    c = b"\x14" + bytes.fromhex("760a01010001c000") + b"McDn"
    c += m._per_length(len(blocks)) + blocks
    gcc = m._GCC_OID + m._per_length(len(c)) + c
    body = b"\x0a\x01\x00\x02\x01\x00"
    body += m._domain((34, 3, 0, 1, 0, 1, 65528, 2)) + m._ber(b"\x04", gcc)
    payload = b"\x02\xf0\x80" + m._ber(b"\x7f\x66", body)
    return b"\x03\x00" + struct.pack(">H", len(payload) + 4) + payload


class Stream:
    def __init__(self, incoming: bytes):
        self.pending = bytearray(incoming)
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        assert timeout > 0

    def recv(self, count):
        chunk = bytes(self.pending[:min(count, 3)])
        del self.pending[:len(chunk)]
        return chunk

    def sendall(self, packet):
        self.sent.append(packet)

    def close(self):
        self.closed = True

    def version(self):
        return "TLSv1.3"


class BoundNLA:
    def __init__(self, delegated: bool):
        self.credentials_delegated = delegated
        self.closed = False

    def close(self):
        self.closed = True


def channel(protocol="CredSSP", incoming=None, *, delegated=True, authorize=None):
    tls = Stream(response() if incoming is None else incoming)
    owner = authorize or (lambda ip, dns: None)
    c = ch.PinnedRDPCredSSPChannel(
        tls, b"pinned-cert",
        ip="192.0.2.3", dns_name="server.example.org", pin="a" * 64,
        selected_protocol=protocol, authorize=owner,
        deadline=time.monotonic() + 5,
    )
    c._credssp = BoundNLA(delegated)
    return c, tls


def test_mcs_creates_real_wire_packet_after_approved_credssp():
    c, tls = channel()
    with c:
        result = c.exchange_mcs_basic_settings(1280, 720)
        assert result.io_channel_id == 1003
        assert result.static_channels == ()
        assert len(tls.sent) == 1
        assert tls.sent[0] == m.build_mcs_connect_initial(
            selected_protocol=2, width=1280, height=720
        )
        with pytest.raises(ch.RDPChannelError, match="requires approved"):
            c.exchange_mcs_basic_settings()
    assert tls.closed


def test_no_credentials_never_sends_mcs_packet():
    c, tls = channel(delegated=False)
    with pytest.raises(ch.RDPChannelError, match="requires approved"):
        c.exchange_mcs_basic_settings()
    assert tls.sent == []
    c.close()


def test_hybrid_ex_requires_exact_early_authorization():
    c, tls = channel("CredSSP-EX", b"\0\0\0\0" + response())
    with c:
        result = c.exchange_mcs_basic_settings()
        assert result.io_channel_id == 1003
        assert tls.sent[0] == m.build_mcs_connect_initial(selected_protocol=8)


@pytest.mark.parametrize("early", [b"\x05\0\0\0", b"\x01\0\0\0", b""])
def test_hybrid_ex_denied_or_truncated_closes_without_send(early):
    c, tls = channel("CredSSP-EX", early)
    with pytest.raises((PermissionError, ch.RDPChannelError)):
        c.exchange_mcs_basic_settings()
    assert tls.closed and not tls.sent


def test_owner_revocation_closes_channel_before_mcs_write():
    def deny(*args):
        raise PermissionError("RDP grant revoked")
    c, tls = channel(authorize=deny)
    with pytest.raises(PermissionError, match="revoked"):
        c.exchange_mcs_basic_settings()
    assert tls.closed and tls.sent == []


def test_truncated_server_reply_closes_connection():
    c, tls = channel(incoming=response()[:9])
    with pytest.raises((ch.RDPChannelError, m.MCSError)):
        c.exchange_mcs_basic_settings()
    assert tls.closed
    assert len(tls.sent) == 1


def test_client_rejects_unauthorized_tls_only_mcs():
    c, tls = channel("TLS")
    with pytest.raises(ch.RDPChannelError, match="security protocol"):
        c.exchange_mcs_basic_settings()
    assert tls.closed and not tls.sent
