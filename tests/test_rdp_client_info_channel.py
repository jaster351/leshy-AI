"""Client Info is only sent once after both MCS joins and owner approval."""
from __future__ import annotations

import time
import pytest

from leshy import rdp_credssp_channel as channel
from leshy.rdp_client_info import build_client_info_pdu
from leshy.rdp_mcs_channels import MCSJoinedChannels


class FakeTLS:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.on_send = None

    def settimeout(self, time_left):
        assert time_left > 0

    def sendall(self, packet):
        self.sent.append(packet)
        if self.on_send:
            self.on_send()

    def close(self):
        self.closed = True


class CredSSP:
    credentials_delegated = True

    def close(self):
        pass


def channel_factory(*, granted=True, joined=True, callback=None):
    tls = FakeTLS()
    owner = callback or (lambda ip, dns: None)
    ch = channel.PinnedRDPCredSSPChannel(
        tls, b"cert", ip="192.0.2.9", dns_name="rdp.example.org",
        pin="a" * 64, selected_protocol="CredSSP",
        authorize=owner, deadline=time.monotonic() + 5,
    )
    ch._credssp = CredSSP() if granted else None
    if joined:
        ch._mcs_joined = MCSJoinedChannels(1007, 1003, (1007, 1003))
    return ch, tls


def test_send_client_info_after_join_and_approval():
    ch, tls = channel_factory()
    with ch:
        result = ch.send_client_info(client_address="192.0.2.50")
        assert result.sent and not result.windows_logon_verified
        assert result.desktop_available is False
        assert result.user_channel_id == 1007
        assert result.io_channel_id == 1003
        assert tls.sent == [build_client_info_pdu(
            1007, 1003, client_address="192.0.2.50"
        )]
        with pytest.raises(channel.RDPChannelError):
            ch.send_client_info(client_address="192.0.2.50")
    assert tls.closed


@pytest.mark.parametrize("approved,joined", [(False, True), (True, False), (False, False)])
def test_unapproved_or_not_joined_sends_nothing(approved, joined):
    ch, tls = channel_factory(granted=approved, joined=joined)
    with pytest.raises(channel.RDPChannelError):
        ch.send_client_info(client_address="192.0.2.50")
    assert tls.sent == []
    ch.close()


def test_invalid_client_address_cannot_write():
    ch, tls = channel_factory()
    with pytest.raises(ValueError):
        ch.send_client_info(client_address="https://untrusted.example")
    assert not tls.sent
    assert tls.closed


def test_owner_revocation_blocks_first_byte():
    def deny(*_):
        raise PermissionError("Revoked owner")
    ch, tls = channel_factory(callback=deny)
    with pytest.raises(PermissionError, match="Revoked"):
        ch.send_client_info(client_address="192.0.2.50")
    assert not tls.sent
    assert tls.closed


def test_revocation_after_send_closes_connection():
    granted = [True]

    def owner(*_):
        if not granted[0]:
            raise PermissionError("Owner revoked")

    ch, tls = channel_factory(callback=owner)
    tls.on_send = lambda: granted.__setitem__(0, False)
    with pytest.raises(PermissionError):
        ch.send_client_info(client_address="192.0.2.50")
    assert len(tls.sent) == 1
    assert tls.closed
    assert ch._client_info_receipt is None


def test_partial_write_fails_closed_without_retry():
    ch, tls = channel_factory()

    def fail_send():
        raise ConnectionError("truncated TLS")
    tls.on_send = fail_send
    with pytest.raises(ConnectionError):
        ch.send_client_info(client_address="192.0.2.50")
    assert ch._client_info_attempted and tls.closed
