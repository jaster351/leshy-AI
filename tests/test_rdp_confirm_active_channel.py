"""Confirm Active can follow licensed Demand Active only, never enable RDP input."""
from __future__ import annotations

import time

import pytest

from leshy import rdp_credssp_channel as channel
from leshy.rdp_client_info import RDPClientInfoReceipt
from leshy.rdp_mcs_channels import MCSJoinedChannels
from leshy.rdp_server_activation import RDPServerActivation
from leshy.rdp_confirm_active import build_confirm_active


class TLS:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.on_send = None

    def settimeout(self, left):
        assert left > 0

    def sendall(self, data):
        self.sent.append(data)
        if self.on_send is not None:
            self.on_send()

    def close(self):
        self.closed = True


class ApprovedNLA:
    credentials_delegated = True

    def close(self):
        pass


def setup(*, activation=True, joined=True, owner=None, display=True):
    tls = TLS()
    conn = channel.PinnedRDPCredSSPChannel(
        tls, b"cert", ip="192.0.2.20", dns_name="rdp.example.org",
        pin="a" * 64, selected_protocol="CredSSP",
        authorize=owner or (lambda *_: None),
        deadline=time.monotonic() + 5,
    )
    conn._credssp = ApprovedNLA()
    if joined:
        conn._mcs_joined = MCSJoinedChannels(1007, 1003, (1007, 1003))
        conn._client_info_receipt = RDPClientInfoReceipt(1007, 1003)
    if activation:
        conn._server_activation = RDPServerActivation(
            share_id=0x103ea, server_mcs_user_id=1002, io_channel_id=1003,
            capability_types=(1, 2),
        )
    if display:
        conn._mcs_display = (1280, 720)
    return conn, tls


def test_confirm_active_on_same_tls_socket_after_license_and_join():
    conn, tls = setup()
    with conn:
        receipt = conn.send_confirm_active()
        assert receipt.sent
        assert not receipt.finalization_complete
        assert not receipt.desktop_available
        assert not receipt.input_enabled
        assert tls.sent == [build_confirm_active(
            share_id=0x103ea, user_channel_id=1007, io_channel_id=1003,
            width=1280, height=720, color_depth=24,
            server_capability_types=(1, 2),
        )]
        with pytest.raises(channel.RDPChannelError):
            conn.send_confirm_active()
    assert tls.closed


@pytest.mark.parametrize("with_activation,with_join,with_display", [
    (False, True, True), (True, False, True), (True, True, False),
    (False, False, False),
])
def test_incomplete_prior_state_never_writes(with_activation, with_join, with_display):
    conn, tls = setup(
        activation=with_activation, joined=with_join, display=with_display
    )
    with pytest.raises(channel.RDPChannelError):
        conn.send_confirm_active()
    assert not tls.sent
    conn.close()


def test_owner_revoked_before_send():
    def denied(*_):
        raise PermissionError("revoked")
    conn, tls = setup(owner=denied)
    with pytest.raises(PermissionError, match="revoked"):
        conn.send_confirm_active()
    assert not tls.sent and tls.closed


def test_owner_revoked_immediately_after_send():
    allowed = [True]

    def authorize(*_):
        if not allowed[0]:
            raise PermissionError("revoked")
    conn, tls = setup(owner=authorize)
    tls.on_send = lambda: allowed.__setitem__(0, False)
    with pytest.raises(PermissionError, match="revoked"):
        conn.send_confirm_active()
    assert len(tls.sent) == 1
    assert tls.closed
    assert conn._confirm_active_receipt is None


def test_transport_error_closes_and_prevents_retry():
    conn, tls = setup()
    def broken():
        raise ConnectionError("partial TLS write")
    tls.on_send = broken
    with pytest.raises(ConnectionError):
        conn.send_confirm_active()
    assert tls.closed
    assert conn._confirm_active_attempted
    assert conn._confirm_active_receipt is None
