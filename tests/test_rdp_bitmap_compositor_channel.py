"""One pinned TLS RDP channel -> complete RGB snapshot or no snapshot at all.

Simulated server bytes only; these tests do not authenticate to Windows.
"""
from __future__ import annotations

import struct
import time

import pytest

from leshy.rdp_credssp_channel import PinnedRDPCredSSPChannel, RDPChannelError
from leshy.rdp_bitmap_update import RDPBitmapUnsupported
from leshy.rdp_finalization import RDPFinalizationStatus
from leshy.rdp_mcs_channels import MCSJoinedChannels
from leshy.rdp_server_activation import RDPServerActivation

SHARE, USER, SERVER, IO = 0x103ea, 1007, 1002, 1003


def bitmap_row(y: int, bgr: bytes, *, flags: int = 0) -> bytes:
    assert len(bgr) == 8
    rectangle = struct.pack("<9H", 0, y, 1, y, 2, 1, 24, flags, len(bgr)) + bgr
    update = struct.pack("<HH", 1, 1) + rectangle
    data = (struct.pack("<HHH", len(update) + 18, 0x17, SERVER)
            + struct.pack("<IBBHBBH", SHARE, 0, 1, len(update) + 4, 2, 0, 0)
            + update)
    mcs = (b"\x02\xf0\x80\x68" + struct.pack(">HH", SERVER - 1001, IO)
           + b"\x70" + bytes((len(data),)) + data)
    return b"\x03\x00" + struct.pack(">H", len(mcs) + 4) + mcs


TOP = bitmap_row(0, bytes.fromhex("0000ff 00ff00 0000"))
BOTTOM = bitmap_row(1, bytes.fromhex("ff0000 ffffff 0000"))


class TLS:
    def __init__(self, data):
        self.pending = bytearray(data)
        self.closed = False
        self.read_count = 0
        self.on_recv = None

    def settimeout(self, remaining):
        assert remaining > 0

    def recv(self, n):
        self.read_count += 1
        if self.on_recv:
            self.on_recv(self.read_count)
        part = bytes(self.pending[:min(3, n)])
        del self.pending[:len(part)]
        return part

    def close(self):
        self.closed = True


class Approved:
    credentials_delegated = True

    def close(self):
        pass


def session(data=TOP + BOTTOM, *, granted=True, finalized=True, authorize=None):
    tls = TLS(data)
    conn = PinnedRDPCredSSPChannel(
        tls, b"certificate", ip="192.0.2.60", dns_name="host.example.org",
        pin="a" * 64, selected_protocol="CredSSP",
        authorize=authorize or (lambda *_: None),
        deadline=time.monotonic() + 5,
    )
    conn._credssp = Approved() if granted else None
    conn._mcs_joined = MCSJoinedChannels(USER, IO, (USER, IO))
    conn._server_activation = RDPServerActivation(SHARE, SERVER, IO, (1, 2))
    conn._mcs_display = (2, 2)
    if finalized:
        conn._finalization_status = RDPFinalizationStatus(
            SHARE, USER, IO, ("synchronize", "cooperate", "granted_control", "font_map")
        )
    return conn, tls


def test_first_patch_never_yields_full_rgb_then_second_does():
    conn, tls = session()
    with conn:
        assert conn.receive_bitmap_frame() is None
        assert conn._bitmap_surface.covered_pixels == 2
        frame = conn.receive_bitmap_frame()
        assert frame.rgb == bytes.fromhex("ff0000 00ff00 0000ff ffffff")
        assert (frame.width, frame.height, frame.update_count) == (2, 2, 2)
        assert not frame.input_enabled
    assert tls.closed
    assert not conn._bitmap_surface.complete


def test_replayed_top_row_does_not_forge_complete_image():
    conn, tls = session(TOP + TOP)
    with conn:
        assert conn.receive_bitmap_frame() is None
        assert conn.receive_bitmap_frame() is None
        assert conn._bitmap_surface.covered_pixels == 2


@pytest.mark.parametrize("granted,finalized", [(False, True), (True, False)])
def test_no_owner_approval_or_finalization_means_no_network_reads(granted, finalized):
    conn, tls = session(granted=granted, finalized=finalized)
    with pytest.raises(RDPChannelError):
        conn.receive_bitmap_frame()
    assert not tls.read_count
    conn.close()


def test_compressed_update_fails_closed_and_clears_old_coverage():
    bad = bitmap_row(0, bytes.fromhex("0000ff 00ff00 0000"), flags=1)
    conn, tls = session(TOP + bad)
    assert conn.receive_bitmap_frame() is None
    assert conn._bitmap_surface.covered_pixels == 2
    with pytest.raises(RDPBitmapUnsupported):
        conn.receive_bitmap_frame()
    assert tls.closed and not conn._bitmap_surface.complete


def test_owner_revocation_after_prior_partial_frame_discards_surface():
    permit = [True]

    def authorize(*_):
        if not permit[0]:
            raise PermissionError("permission revoked")

    conn, tls = session(authorize=authorize)
    assert conn.receive_bitmap_frame() is None
    permit[0] = False
    with pytest.raises(PermissionError):
        conn.receive_bitmap_frame()
    assert tls.closed
    assert not conn._bitmap_surface.complete


def test_truncated_second_update_fails_without_releasing_pixels():
    conn, tls = session(TOP + BOTTOM[:-1])
    assert conn.receive_bitmap_frame() is None
    with pytest.raises(RDPChannelError):
        conn.receive_bitmap_frame()
    assert tls.closed and not conn._bitmap_surface.complete
