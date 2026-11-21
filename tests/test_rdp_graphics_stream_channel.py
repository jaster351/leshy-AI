"""Byte-structured Bitmap Update -> stream callbacks, simulated TLS only."""
from __future__ import annotations
import socket
import struct
import time
import pytest

from leshy.rdp_credssp_channel import PinnedRDPCredSSPChannel, RDPChannelError
from leshy.rdp_finalization import RDPFinalizationStatus
from leshy.rdp_graphics_stream import GraphicsStreamPolicy, RDPGraphicsStreamError
from leshy.rdp_mcs_channels import MCSJoinedChannels
from leshy.rdp_server_activation import RDPServerActivation

SHARE, USER, SERVER, IO = 0x103ea, 1007, 1002, 1003

def bitmap_row(y, bgr):
    rect = struct.pack("<9H", 0,y,1,y,2,1,24,0,len(bgr))+bgr
    update = struct.pack("<HH", 1,1)+rect
    data = (struct.pack("<HHH",len(update)+18,0x17,SERVER)
            +struct.pack("<IBBHBBH",SHARE,0,1,len(update)+4,2,0,0)+update)
    mcs = b"\x02\xf0\x80\x68"+struct.pack(">HH",SERVER-1001,IO)+b"\x70"+bytes([len(data)])+data
    return b"\x03\x00"+struct.pack(">H",len(mcs)+4)+mcs

TOP=bitmap_row(0,bytes.fromhex("0000ff 00ff00 0000"))
BOTTOM=bitmap_row(1,bytes.fromhex("ff0000 ffffff 0000"))

class TLS:
    def __init__(self, data):
        self.buffer=bytearray(data)
        self.reads=0
        self.consumed=0
        self.closed=False
        self.on_recv=None
    def settimeout(self, value):
        assert value>0
    def recv(self, count):
        self.reads+=1
        if self.on_recv: self.on_recv()
        part=bytes(self.buffer[:min(count,3)])
        del self.buffer[:len(part)]
        self.consumed+=len(part)
        return part
    def close(self):
        self.closed=True

class Approved:
    credentials_delegated=True
    def close(self): pass

def channel(data=TOP+BOTTOM, *, authorize=None, finalized=True, credential=True):
    tls=TLS(data)
    conn=PinnedRDPCredSSPChannel(
        tls,b"cert",ip="192.0.2.55",dns_name="owned.example.test",
        pin="a"*64,selected_protocol="CredSSP",
        authorize=authorize or (lambda *_:None),
        deadline=time.monotonic()+5,
    )
    if credential: conn._credssp=Approved()
    conn._mcs_joined=MCSJoinedChannels(USER,IO,(USER,IO))
    conn._server_activation=RDPServerActivation(SHARE,SERVER,IO,(1,2))
    conn._mcs_display=(2,2)
    if finalized:
        conn._finalization_status=RDPFinalizationStatus(
            SHARE,USER,IO,("synchronize","cooperate","granted_control","font_map"))
    return conn,tls

def policy(**kwargs):
    params=dict(max_duration=4,idle_timeout=1,max_updates=2,min_frame_interval=0)
    params.update(kwargs)
    return GraphicsStreamPolicy(**params)

def test_complete_screen_only_after_two_updates():
    conn,tls=channel(); frames=[]
    result=conn.stream_bitmap_frames(frames.append,policy=policy())
    assert (result.received_updates,result.published_frames,result.reason)==(2,1,"update_limit")
    assert not result.desktop_verified and not result.input_enabled
    assert len(frames)==1 and frames[0].rgb==bytes.fromhex("ff0000 00ff00 0000ff ffffff")
    assert tls.closed and not conn._bitmap_surface.complete

def test_duplicate_partial_updates_never_fake_screen():
    conn,tls=channel(TOP+TOP); frames=[]
    result=conn.stream_bitmap_frames(frames.append,policy=policy())
    assert result.received_updates==2 and result.published_frames==0 and frames==[] and tls.closed

def test_stop_from_callback_does_not_consume_next_packet():
    conn,tls=channel(TOP+BOTTOM+TOP); frames=[]
    def sink(frame):
        frames.append(frame)
        conn.stop_graphics_stream()
    result=conn.stream_bitmap_frames(sink,policy=policy(max_updates=3))
    assert result.reason=="stopped" and result.published_frames==1
    assert len(frames)==1 and tls.buffer==bytearray(TOP) and tls.closed

def test_stop_during_fragmented_packet_no_frame_leaks():
    conn,tls=channel();frames=[]
    tls.on_recv=lambda:conn.stop_graphics_stream() if tls.reads==3 else None
    result=conn.stream_bitmap_frames(frames.append,policy=policy())
    assert result.reason=="stopped" and frames==[] and tls.closed

def test_revocation_during_second_packet_never_publishes():
    state=[True]
    def authorize(*_):
        if not state[0]: raise PermissionError("revoked")
    conn,tls=channel(authorize=authorize)
    tls.on_recv=lambda:state.__setitem__(0,False) if tls.consumed>len(TOP)+3 else None
    frames=[]
    with pytest.raises(PermissionError,match="revoked"):
        conn.stream_bitmap_frames(frames.append,policy=policy())
    assert frames==[] and tls.closed and not conn._bitmap_surface.complete

@pytest.mark.parametrize("finalized,credential",[(False,True),(True,False)])
def test_unauthorized_stream_never_reads(finalized,credential):
    conn,tls=channel(finalized=finalized,credential=credential)
    with pytest.raises(RDPChannelError):
        conn.stream_bitmap_frames(lambda _:None,policy=policy())
    assert tls.reads==0
    conn.close()

def test_truncated_pixel_update_closes_without_publishing():
    conn,tls=channel(TOP+BOTTOM[:-1])
    with pytest.raises(RDPGraphicsStreamError):
        conn.stream_bitmap_frames(lambda _:pytest.fail("leaked pixels"),policy=policy())
    assert tls.closed and not conn._bitmap_surface.complete

def test_callback_exception_closes_and_prevents_retry():
    conn,tls=channel()
    def sink(_): raise RuntimeError("sink failure")
    with pytest.raises(RuntimeError,match="sink failure"):
        conn.stream_bitmap_frames(sink,policy=policy())
    assert tls.closed
    with pytest.raises(RDPChannelError):
        conn.stream_bitmap_frames(lambda _:None,policy=policy())

def test_invalid_callback_rejected_before_read():
    conn,tls=channel()
    with pytest.raises(TypeError):
        conn.stream_bitmap_frames(None,policy=policy())
    assert tls.reads==0
    conn.close()


def test_stop_on_last_permitted_frame_reports_stopped():
    conn, tls = channel()
    frames = []

    def sink(frame):
        frames.append(frame)
        conn.stop_graphics_stream()

    result = conn.stream_bitmap_frames(sink, policy=policy(max_updates=2))
    assert result.reason == "stopped"
    assert result.published_frames == len(frames) == 1
    assert tls.closed


def test_decode_crossing_total_deadline_cannot_publish(monkeypatch):
    import leshy.rdp_credssp_channel as channel_module
    from types import SimpleNamespace
    conn, tls = channel()
    real_parse = channel_module.parse_bitmap_update
    now = [time.monotonic()]
    monkeypatch.setattr(channel_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls = [0]

    def parse(*args, **kwargs):
        result = real_parse(*args, **kwargs)
        calls[0] += 1
        if calls[0] == 2:
            now[0] += 5.0
        return result

    monkeypatch.setattr(channel_module, "parse_bitmap_update", parse)
    frames = []
    result = conn.stream_bitmap_frames(frames.append, policy=policy(max_duration=4))
    assert result.reason == "duration_limit"
    assert result.received_updates == 2
    assert result.published_frames == 0 and not frames
    assert tls.closed and not conn._bitmap_surface.complete
