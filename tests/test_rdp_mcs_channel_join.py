"""Attach+join user/I/O channels on a single existing TLS RDP transport."""
from __future__ import annotations

import time
import pytest

from leshy import rdp_credssp_channel as channel
from leshy.rdp_mcs_basic import ServerMCSSettings
from leshy.rdp_mcs_channels import MCSChannelError


ATTACH = bytes.fromhex("0300000b02f0802e000006")
JOIN_USER = bytes.fromhex("0300000f02f0803e00000603ef03ef")
JOIN_IO = bytes.fromhex("0300000f02f0803e00000603eb03eb")


class FakeTLS:
    def __init__(self, data=b""):
        self.data = bytearray(data)
        self.sent = []
        self.closed = False
        self.send_hook = None

    def settimeout(self, remaining):
        assert remaining > 0

    def recv(self, n):
        value = bytes(self.data[:min(3, n)])
        del self.data[:len(value)]
        return value

    def sendall(self, data):
        self.sent.append(data)
        if self.send_hook:
            self.send_hook()

    def close(self):
        self.closed = True


class ApprovedCredSSP:
    def __init__(self, ready=True):
        self.credentials_delegated = ready
        self.closed = False

    def close(self):
        self.closed = True


def make_channel(data=ATTACH + JOIN_USER + JOIN_IO, *, approved=True,
                 settings=True, authorize=None):
    tls = FakeTLS(data)
    c = channel.PinnedRDPCredSSPChannel(
        tls, b"cert", ip="192.0.2.9",
        dns_name="host.example.org", pin="a" * 64,
        selected_protocol="CredSSP",
        authorize=authorize or (lambda ip, dns: None),
        deadline=time.monotonic() + 5,
    )
    c._credssp = ApprovedCredSSP(approved)
    if settings:
        c._mcs_settings = ServerMCSSettings(1003, (), 0x00080004, 0x0B)
    return c, tls


def test_attaches_and_joins_only_user_then_io_channel():
    c, tls = make_channel()
    with c:
        result = c.join_mcs_channels()
        assert result.user_channel_id == 1007
        assert result.io_channel_id == 1003
        assert result.joined_channel_ids == (1007, 1003)
        assert result.desktop_available is False
        assert [p.hex() for p in tls.sent] == [
            "0300000c02f0800401000100",
            "0300000802f08028",
            "0300000c02f08038000603ef",
            "0300000c02f08038000603eb",
        ]
        assert len(tls.data) == 0
        with pytest.raises(channel.RDPChannelError):
            c.join_mcs_channels()
    assert tls.closed


@pytest.mark.parametrize("ready,settings", [(False, True), (True, False)])
def test_preapproval_required_before_any_network_write(ready, settings):
    c, tls = make_channel(approved=ready, settings=settings)
    with pytest.raises(channel.RDPChannelError):
        c.join_mcs_channels()
    assert not tls.sent
    c.close()


@pytest.mark.parametrize("incoming", [
    bytes.fromhex("0300000b02f0802e010006") + JOIN_USER + JOIN_IO,
    ATTACH + bytes.fromhex("0300000f02f0803e00000603eb03ef") + JOIN_IO,
    ATTACH + JOIN_USER + bytes.fromhex("0300000f02f0803e00000603ef03ef"),
    ATTACH + JOIN_USER[:-2],
])
def test_incorrect_or_missing_channel_confirmation_fails_closed(incoming):
    c, tls = make_channel(incoming)
    with pytest.raises((MCSChannelError, channel.RDPChannelError)):
        c.join_mcs_channels()
    assert tls.closed


def test_owner_revoked_after_first_pdu_closes_before_attach():
    allowed = [True]

    def authorize(ip, dns):
        assert (ip, dns) == ("192.0.2.9", "host.example.org")
        if not allowed[0]:
            raise PermissionError("RDP access revoked")

    c, tls = make_channel(authorize=authorize)
    tls.send_hook = lambda: allowed.__setitem__(0, False)
    with pytest.raises(PermissionError, match="revoked"):
        c.join_mcs_channels()
    assert len(tls.sent) == 1
    assert tls.closed


def test_invalid_io_id_aborts_without_join_requests():
    c, tls = make_channel()
    c._mcs_settings = ServerMCSSettings(1007, (), 0x80004, 0x0B)
    with pytest.raises(MCSChannelError):
        c.join_mcs_channels()
    assert [p[7] for p in tls.sent] == [0x04, 0x28]
    assert tls.closed
