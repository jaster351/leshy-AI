"""Local fake-TLS tests for license -> Demand Active sequencing and failure."""
from __future__ import annotations
import struct
import time

import pytest

from leshy import rdp_credssp_channel as channel
from leshy.rdp_client_info import RDPClientInfoReceipt
from leshy.rdp_mcs_channels import MCSJoinedChannels
from leshy.rdp_server_activation import RDPActivationError


def _indication(body: bytes, *, origin=1002, dest=1003):
    length = bytes((len(body),)) if len(body) < 128 else bytes((0x80 | (len(body) >> 8), len(body) & 255))
    payload = b"\x02\xf0\x80\x68" + struct.pack(">HH", origin - 1001, dest) + b"\x70" + length + body
    return b"\x03\x00" + struct.pack(">H", len(payload) + 4) + payload


def _license():
    return bytes.fromhex("80000000ff031000070000000200000004000000")


def _demand():
    caps = b"\x01\x00\x04\x00\x02\x00\x04\x00"
    body = (struct.pack("<IHH", 0x000103ea, 4, 12)
            + b"RDP\x00" + struct.pack("<HH", 2, 0) + caps
            + struct.pack("<I", 0))
    return struct.pack("<HHH", 6 + len(body), 0x0011, 1002) + body


class Stream:
    def __init__(self, payload):
        self.data = bytearray(payload)
        self.closed = False
        self.reads = 0

    def settimeout(self, remaining):
        assert remaining > 0

    def recv(self, count):
        self.reads += 1
        part = bytes(self.data[:min(count, 3)])
        del self.data[:len(part)]
        return part

    def close(self):
        self.closed = True


class NLA:
    credentials_delegated = True

    def close(self):
        pass


def make_channel(payload=None, *, valid=True, owner=None):
    if payload is None:
        payload = _indication(_license()) + _indication(_demand())
    tls = Stream(payload)
    c = channel.PinnedRDPCredSSPChannel(
        tls, b"certificate", ip="192.0.2.5", dns_name="owned.example.org",
        pin="a" * 64, selected_protocol="CredSSP",
        authorize=owner or (lambda *_: None), deadline=time.monotonic() + 5,
    )
    c._credssp = NLA()
    c._mcs_joined = MCSJoinedChannels(1007, 1003, (1007, 1003))
    if valid:
        c._client_info_receipt = RDPClientInfoReceipt(1007, 1003)
    return c, tls


def test_valid_client_then_demand_active_but_no_desktop():
    conn, tls = make_channel()
    with conn:
        result = conn.receive_server_activation()
        assert result.license_valid_client
        assert result.share_id == 0x000103ea
        assert result.capability_types == (1, 2)
        assert result.io_channel_id == 1003
        assert not result.desktop_available
        assert not result.confirmed_active
        assert not tls.data
        with pytest.raises(channel.RDPChannelError):
            conn.receive_server_activation()
    assert tls.closed


def test_no_client_info_never_reads_network():
    conn, tls = make_channel(valid=False)
    with pytest.raises(channel.RDPChannelError):
        conn.receive_server_activation()
    assert tls.reads == 0
    conn.close()


@pytest.mark.parametrize("wire", [
    _indication(_license(), dest=1004) + _indication(_demand()),
    _indication(_license()) + _indication(_demand(), origin=1004),
    _indication(_license()[:-1]) + _indication(_demand()),
    _indication(_license()) + _indication(_demand()[:-1]),
    _indication(_license()) + _indication(_demand())[:8],
])
def test_mismatch_or_truncated_server_activation_closes(wire):
    conn, tls = make_channel(wire)
    with pytest.raises((RDPActivationError, channel.RDPChannelError)):
        conn.receive_server_activation()
    assert tls.closed
    assert conn._server_activation is None


def test_owner_revoked_mid_read_closes_tls():
    checked = [0]

    def authorize(ip, dns):
        checked[0] += 1
        if checked[0] == 3:
            raise PermissionError("revoked")

    conn, tls = make_channel(owner=authorize)
    with pytest.raises(PermissionError):
        conn.receive_server_activation()
    assert tls.closed
    assert conn._server_activation is None
