"""Bounded bitmap updates require finalization and renewed owner authorization.

These tests use simulated TLS byte streams, NOT a working Windows desktop.
"""
from __future__ import annotations

import struct
import time

import pytest

from leshy.rdp_credssp_channel import PinnedRDPCredSSPChannel, RDPChannelError
from leshy.rdp_bitmap_update import RDPBitmapError, RDPBitmapUnsupported
from leshy.rdp_finalization import RDPFinalizationStatus
from leshy.rdp_mcs_channels import MCSJoinedChannels
from leshy.rdp_server_activation import RDPServerActivation

SHARE, USER, SERVER, IO = 0x103ea, 1007, 1002, 1003


def bitmap_packet(*, flags=0, share=SHARE):
    # 2x2 BGR24; bottom row blue/white, top row red/green.
    pixels = bytes.fromhex("ff0000 ffffff aabb 0000ff 00ff00 0102")
    rect = struct.pack("<9H", 0, 0, 1, 1, 2, 2, 24, flags, len(pixels)) + pixels
    update = struct.pack("<HH", 1, 1) + rect
    share_data = (struct.pack("<HHH", len(update) + 18, 0x17, SERVER)
                  + struct.pack("<IBBHBBH", share, 0, 1, len(update) + 4,
                                2, 0, 0) + update)
    mcs = (b"\x02\xf0\x80\x68" + struct.pack(">HH", SERVER - 1001, IO)
           + b"\x70" + bytes([len(share_data)]) + share_data)
    return b"\x03\x00" + struct.pack(">H", len(mcs) + 4) + mcs


class SimulatedTLS:
    def __init__(self, incoming=b""):
        self.incoming = bytearray(incoming)
        self.closed = False
        self.read_count = 0
        self.on_recv = None

    def settimeout(self, seconds):
        assert seconds > 0

    def recv(self, count):
        self.read_count += 1
        if self.on_recv:
            self.on_recv(self.read_count)
        result = bytes(self.incoming[:min(count, 3)])
        del self.incoming[:len(result)]
        return result

    def close(self):
        self.closed = True


class ApprovedCredSSP:
    credentials_delegated = True

    def close(self):
        pass


def make_channel(incoming=None, *, finalized=True, authorize=None):
    tls = SimulatedTLS(bitmap_packet() if incoming is None else incoming)
    connection = PinnedRDPCredSSPChannel(
        tls, b"pinned", ip="192.0.2.99", dns_name="rdp.example.org",
        pin="a" * 64, selected_protocol="CredSSP",
        authorize=authorize or (lambda *_: None),
        deadline=time.monotonic() + 5,
    )
    connection._credssp = ApprovedCredSSP()
    connection._mcs_joined = MCSJoinedChannels(USER, IO, (USER, IO))
    connection._server_activation = RDPServerActivation(
        SHARE, SERVER, IO, (1, 2),
    )
    connection._mcs_display = (64, 64)
    if finalized:
        connection._finalization_status = RDPFinalizationStatus(
            SHARE, USER, IO,
            ("synchronize", "cooperate", "granted_control", "font_map"),
        )
    return connection, tls


def test_read_actual_rgb_patch_only_after_finalization():
    conn, tls = make_channel()
    with conn:
        (patch,) = conn.receive_bitmap_rectangles()
        assert (patch.left, patch.top, patch.width, patch.height) == (0, 0, 2, 2)
        assert patch.rgb == bytes.fromhex("ff0000 00ff00 0000ff ffffff")
        assert patch.pixel_format == "RGB"
        assert len(tls.incoming) == 0
    assert tls.closed


def test_unfinalized_connection_never_reads_pixels():
    conn, tls = make_channel(finalized=False)
    with pytest.raises(RDPChannelError, match="finalized"):
        conn.receive_bitmap_rectangles()
    assert tls.read_count == 0
    conn.close()


def test_two_updates_can_be_read_without_inventing_full_frame():
    conn, tls = make_channel(incoming=bitmap_packet() + bitmap_packet())
    with conn:
        a = conn.receive_bitmap_rectangles()
        b = conn.receive_bitmap_rectangles()
        assert a == b
        assert len(tls.incoming) == 0


@pytest.mark.parametrize("incoming", [
    bitmap_packet(flags=1),
    bitmap_packet(share=0x123),
    bitmap_packet()[:-1],
    bitmap_packet() + b"\0",  # two valid/unread frames need another call
])
def test_unsupported_or_corrupt_updates_fail_closed(incoming):
    conn, tls = make_channel(incoming)
    if incoming == bitmap_packet() + b"\0":
        # Deliberately do not interpret trailing TLS data as part of one
        # length-delimited TPKT frame.
        with conn:
            conn.receive_bitmap_rectangles()
            assert tls.incoming == bytearray(b"\0")
    else:
        with pytest.raises((RDPBitmapError, RDPBitmapUnsupported, RDPChannelError)):
            conn.receive_bitmap_rectangles()
        assert tls.closed


def test_revocation_before_first_receive_never_reads():
    def deny(*_):
        raise PermissionError("owner revoked")
    conn, tls = make_channel(authorize=deny)
    with pytest.raises(PermissionError, match="revoked"):
        conn.receive_bitmap_rectangles()
    assert not tls.read_count
    assert tls.closed


def test_owner_revocation_during_packet_receive_blocks_pixel_release():
    allowed = [True]

    def authorize(*_):
        if not allowed[0]:
            raise PermissionError("revoked during read")
    conn, tls = make_channel(authorize=authorize)
    tls.on_recv = lambda n: allowed.__setitem__(0, False) if n == 4 else None
    with pytest.raises(PermissionError):
        conn.receive_bitmap_rectangles()
    assert tls.closed
