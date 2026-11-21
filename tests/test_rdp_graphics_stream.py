import math
import socket
import time
import pytest
from leshy.rdp_graphics_stream import (
    GraphicsStreamPolicy, RDPGraphicsStreamError, RDPGraphicsStopped,
    RDPGraphicsTimeout, read_graphics_tpkt,
)

FRAME = b"\x03\x00\x00\x10" + b"\x02\xf0\x80\x68" + b"\x00" * 8


class TLS:
    def __init__(self, data=FRAME, *, chunk=2):
        self.buffer = bytearray(data)
        self.chunk = chunk
        self.reads = 0
        self.timeout = 0
        self.inject_timeout = 0

    def settimeout(self, value):
        assert 0 < value <= 0.2
        self.timeout = value

    def recv(self, size):
        self.reads += 1
        if self.inject_timeout:
            self.inject_timeout -= 1
            raise socket.timeout()
        result = bytes(self.buffer[:min(size, self.chunk)])
        del self.buffer[:len(result)]
        return result


def read(tls, authorized=lambda: None, stopped=lambda: False, deadline=None):
    return read_graphics_tpkt(
        tls, deadline=deadline if deadline is not None else time.monotonic()+2,
        reauthorize=authorized, stopped=stopped,
    )


def test_exact_fragmented_frame_without_consuming_next():
    tls = TLS(FRAME + FRAME)
    assert read(tls) == FRAME
    assert tls.buffer == bytearray(FRAME)
    assert read(tls) == FRAME


def test_one_timed_out_tls_poll_can_retry():
    tls=TLS()
    tls.inject_timeout=2
    assert read(tls)==FRAME


def test_stop_before_first_byte_blocks_all_reads():
    tls=TLS()
    with pytest.raises(RDPGraphicsStopped):
        read(tls, stopped=lambda: True)
    assert tls.reads==0


def test_revoke_mid_frame_prevents_publishing():
    tls=TLS()
    def revoke():
        if tls.reads>=2:
            raise PermissionError("revoked")
    with pytest.raises(PermissionError):
        read(tls, authorized=revoke)
    assert tls.buffer


@pytest.mark.parametrize('wire', [
    FRAME[:-1], b"\x03\x00\x00\x0f" + b"\0" * 11,
    b"\x03\x00\xff\xff" + b"\0" * 12,
    b"\x04\x00\x00\x10" + b"\0" * 12,
])
def test_invalid_or_partial_tpkt_rejected(wire):
    with pytest.raises(RDPGraphicsStreamError):
        read(TLS(wire))


def test_elapsed_deadline_raises_without_read():
    tls=TLS()
    with pytest.raises(RDPGraphicsTimeout):
        read(tls,deadline=time.monotonic()-0.1)
    assert tls.reads==0


@pytest.mark.parametrize('params', [
    {'max_duration': float('nan')}, {'max_duration': True},
    {'max_duration': float('inf')}, {'max_duration': 0},
    {'idle_timeout': float('nan')}, {'idle_timeout': 0.1},
    {'min_frame_interval': float('inf')}, {'min_frame_interval': -1},
    {'max_updates': True}, {'max_updates': 0}, {'max_updates': 10001},
])
def test_invalid_policy_rejected(params):
    with pytest.raises(ValueError):
        GraphicsStreamPolicy(**params)


def test_policy_defaults_bound_and_status_safely():
    p=GraphicsStreamPolicy()
    assert p.max_duration==300 and p.max_updates<=10000


def test_final_authorization_cannot_publish_expired_packet(monkeypatch):
    import leshy.rdp_graphics_stream as stream
    from types import SimpleNamespace
    now = [100.0]
    monkeypatch.setattr(stream, "time", SimpleNamespace(monotonic=lambda: now[0]))
    tls = TLS(chunk=16)

    def authorize():
        if not tls.buffer:
            now[0] = 102.0

    with pytest.raises(RDPGraphicsTimeout):
        read(tls, authorized=authorize, deadline=101.0)
    assert not tls.buffer
