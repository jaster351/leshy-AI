"""Fake-TLS tests for finalization gating, exact protocol order and revocation.

These do not establish a real Windows RDP desktop session.
"""
from __future__ import annotations

import struct
import time

import pytest

from leshy import rdp_credssp_channel as channel
from leshy.rdp_confirm_active import ConfirmActiveReceipt
from leshy.rdp_finalization import (
    RDPFinalizationError, client_finalization_packets,
)
from leshy.rdp_mcs_channels import MCSJoinedChannels
from leshy.rdp_server_activation import RDPServerActivation


SHARE, USER, IO, SERVER = 0x103ea, 1007, 1003, 1002


def _frame(kind: int, body: bytes, *, source=SERVER, share=SHARE, sender=SERVER, io=IO):
    data = (struct.pack("<HHH", 18 + len(body), 0x17, source)
            + struct.pack("<IBBHBBH", share, 0, 2, 4 + len(body), kind, 0, 0)
            + body)
    mcs = b"\x68" + struct.pack(">HH", sender - 1001, io) + b"\x70" + bytes([len(data)]) + data
    return b"\x03\x00" + struct.pack(">H", 7 + len(mcs)) + b"\x02\xf0\x80" + mcs


def _responses(*, wrong_grant=False):
    return b"".join((
        _frame(0x1f, struct.pack("<HH", 1, SERVER)),
        _frame(0x14, struct.pack("<HHI", 4, 0, 0)),
        _frame(0x14, struct.pack("<HHI", 2, USER + int(wrong_grant), SERVER)),
        _frame(0x28, struct.pack("<HHHH", 0, 0, 3, 4)),
    ))


class Stream:
    def __init__(self, incoming: bytes):
        self.incoming = bytearray(incoming)
        self.sent = []
        self.closed = False
        self.on_send = None
        self.count = 0

    def settimeout(self, remaining):
        assert remaining > 0

    def sendall(self, packet):
        self.count += 1
        self.sent.append(packet)
        if self.on_send:
            self.on_send(self.count)

    def recv(self, amount):
        part = bytes(self.incoming[:min(3, amount)])
        del self.incoming[:len(part)]
        return part

    def close(self):
        self.closed = True


class ApprovedCredSSP:
    credentials_delegated = True

    def close(self):
        pass


def make_connection(*, response=None, ready=True, authorize=None):
    tls = Stream(_responses() if response is None else response)
    conn = channel.PinnedRDPCredSSPChannel(
        tls, b"cert", ip="192.0.2.33", dns_name="rdp.example.net",
        pin="a" * 64, selected_protocol="CredSSP",
        authorize=authorize or (lambda *_: None),
        deadline=time.monotonic() + 5,
    )
    conn._credssp = ApprovedCredSSP()
    conn._mcs_joined = MCSJoinedChannels(USER, IO, (USER, IO))
    conn._server_activation = RDPServerActivation(
        SHARE, SERVER, IO, (1, 2),
    )
    if ready:
        conn._confirm_active_receipt = ConfirmActiveReceipt(
            SHARE, USER, IO,
        )
    return conn, tls


def test_successful_four_step_finalization_remains_desktop_disabled():
    conn, tls = make_connection()
    with conn:
        status = conn.finalize_mcs_connection()
        assert status.protocol_finalized
        assert not status.desktop_available
        assert not status.input_enabled
        assert status.acknowledged_pdus == (
            "synchronize", "cooperate", "granted_control", "font_map"
        )
        assert tls.sent == list(client_finalization_packets(
            share_id=SHARE, user_channel_id=USER, io_channel_id=IO,
        ))
        assert tls.incoming == b""
        with pytest.raises(channel.RDPChannelError, match="Confirm Active"):
            conn.finalize_mcs_connection()
    assert tls.closed


def test_no_confirm_active_never_sends_pdu():
    conn, tls = make_connection(ready=False)
    with pytest.raises(channel.RDPChannelError, match="Confirm Active"):
        conn.finalize_mcs_connection()
    assert not tls.sent
    conn.close()


@pytest.mark.parametrize("invalid_response", [
    _responses(wrong_grant=True),
    _responses()[:-2],
    _responses()[:10],
    _frame(0x14, struct.pack("<HHI", 4, 0, 0)) + _responses(),
])
def test_wrong_reply_or_broken_tls_closes_fully(invalid_response):
    conn, tls = make_connection(response=invalid_response)
    with pytest.raises((RDPFinalizationError, channel.RDPChannelError)):
        conn.finalize_mcs_connection()
    assert tls.closed and conn._finalization_status is None


def test_wrong_activation_identity_never_sends():
    conn, tls = make_connection()
    conn._server_activation = RDPServerActivation(
        SHARE + 1, SERVER, IO, (1, 2),
    )
    with pytest.raises(RDPFinalizationError):
        conn.finalize_mcs_connection()
    assert not tls.sent and tls.closed


def test_owner_revocation_mid_batch_stops_before_next_pdu():
    enabled = [True]

    def owner(ip, dns):
        if not enabled[0]:
            raise PermissionError("revoked")

    conn, tls = make_connection(authorize=owner)
    tls.on_send = lambda count: enabled.__setitem__(0, False) if count == 2 else None
    with pytest.raises(PermissionError, match="revoked"):
        conn.finalize_mcs_connection()
    assert len(tls.sent) == 2
    assert tls.closed
    assert conn._finalization_status is None


def test_owner_revocation_mid_responses_closes_and_does_not_publish():
    checks = [0]
    def authorize(ip, dns):
        checks[0] += 1
        if checks[0] >= 11:
            raise PermissionError("revoked")
    conn, tls = make_connection(authorize=authorize)
    with pytest.raises(PermissionError, match="revoked"):
        conn.finalize_mcs_connection()
    assert tls.closed and conn._finalization_status is None
