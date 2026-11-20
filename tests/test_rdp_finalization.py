"""Byte-level protocol tests without network, credentials, or RDP server."""
import struct
import pytest
from leshy.rdp_finalization import (client_finalization_packets, parse_server_finalization,
                                    RDPFinalizationError)

SHARE = 0x103ea
USER = 1007
IO = 1003
SERVER = 1002
STAGES = ("synchronize", "cooperate", "granted_control", "font_map")


def data_pdu(kind, payload, *, source=SERVER, share=SHARE, comp=0, csize=0,
             stream=1, uncompressed=None):
    n = 18 + len(payload)
    return (struct.pack("<HHH", n, 0x17, source)
            + struct.pack("<IBBHBBH", share, 0, stream,
                          len(payload)+4 if uncompressed is None else uncompressed,
                          kind, comp, csize) + payload)


def indicated(data, *, origin=SERVER, io=IO):
    length = bytes((len(data),)) if len(data)<128 else bytes((0x80 | (len(data) >> 8), len(data)&255))
    mcs = b"\x68"+struct.pack(">HH", origin-1001, io) + b"\x70" + length + data
    return b"\x03\x00" + struct.pack(">H", 7+len(mcs)) + b"\x02\xf0\x80" + mcs


def response(stage):
    if stage == "synchronize":
        pdu=data_pdu(0x1f, struct.pack("<HH", 1, 0x4444), stream=0, uncompressed=0x16)
    elif stage == "cooperate":
        pdu=data_pdu(0x14, struct.pack("<HHI", 4, 0, 0), stream=2)
    elif stage == "granted_control":
        pdu=data_pdu(0x14, struct.pack("<HHI", 2, USER, 1002), stream=2)
    else:
        pdu=data_pdu(0x28, struct.pack("<HHHH", 0, 0, 3, 4), stream=2)
    return indicated(pdu)


def check(packet, stage):
    return parse_server_finalization(packet, expected=stage, share_id=SHARE,
                                     server_user_id=SERVER, user_channel_id=USER,
                                     io_channel_id=IO)


def test_four_client_packets_in_strict_order_with_share_data_headers():
    packets = client_finalization_packets(share_id=SHARE, user_channel_id=USER,
                                          io_channel_id=IO)
    assert len(packets)==4
    for wire, kind, payload in zip(packets, (0x1f, 0x14, 0x14, 0x27),
                                   (b"\x01\x00\xea\x03", b"\x04\x00\0\0\0\0\0\0",
                                    b"\x01\x00\0\0\0\0\0\0", b"\0\0\0\0\x03\0\x32\0")):
        assert wire[:8] == b"\x03\x00"+len(wire).to_bytes(2,"big")+b"\x02\xf0\x80\x64"
        assert struct.unpack_from(">HH",wire,8)==(6,1003)
        assert wire[12]==0x70
        assert wire[13]==len(wire)-14
        body=wire[14:]
        assert struct.unpack_from("<HHH",body,0)==(len(body),0x17,1007)
        assert struct.unpack_from("<IBBHBBH",body,6)==(SHARE,0,1,len(payload)+4,kind,0,0)
        assert body[18:]==payload
        assert b"\x80\x00\x00\x00" not in wire[14:18]

@pytest.mark.parametrize("stage", STAGES)
def test_valid_server_confirmations(stage):
    check(response(stage), stage)

@pytest.mark.parametrize("stage", STAGES)
def test_out_of_order_server_confirmations(stage):
    for wrong in STAGES:
        if wrong != stage:
            with pytest.raises(RDPFinalizationError):
                check(response(wrong),stage)

@pytest.mark.parametrize("mutator", [
    lambda p: p[:-1],
    lambda p: p+b"\x00",
    lambda p: b"\x03\x00\0\x10"+p[4:],
    lambda p: p[:7] + b"\x64"+p[8:],
    lambda p: p[:10] + b"\x03\xec" +p[12:],
    lambda p: p[:14] + b"\x00\x00" +p[16:],
])
def test_bad_framing_rejected(mutator):
    with pytest.raises(RDPFinalizationError):
        check(mutator(response("font_map")),"font_map")

@pytest.mark.parametrize("kwargs", [
    {"user_channel_id": 1000}, {"io_channel_id":1000},
    {"share_id":0}, {"share_id":True}, {"share_id":2**32},
    {"user_channel_id":True}, {"io_channel_id":True},
    {"io_channel_id":USER},
])
def test_invalid_client_identifiers_rejected(kwargs):
    args=dict(share_id=SHARE,user_channel_id=USER,io_channel_id=IO)
    args.update(kwargs)
    with pytest.raises(RDPFinalizationError):
        client_finalization_packets(**args)

@pytest.mark.parametrize("stage,invalid", [
    ("synchronize",data_pdu(0x1f,struct.pack("<HH",2,SERVER))),
    ("cooperate",data_pdu(0x14,struct.pack("<HHI",2,0,0))),
    ("granted_control",data_pdu(0x14,struct.pack("<HHI",2,USER+1,SERVER))),
    ("granted_control",data_pdu(0x14,struct.pack("<HHI",2,USER,SERVER+1))),
    ("font_map",data_pdu(0x28,struct.pack("<HHHH",0,0,0,4))),
    ("font_map",data_pdu(0x28,struct.pack("<HHHH",0,0,3,50))),
    ("font_map",data_pdu(0x28,struct.pack("<HHHH",1,0,3,4))),
    ("font_map",data_pdu(0x28,struct.pack("<HHHH",0,0,3,4),comp=0x20)),
    ("font_map",data_pdu(0x28,struct.pack("<HHHH",0,0,3,4),csize=1)),
    ("font_map",data_pdu(0x28,struct.pack("<HHHH",0,0,3,4),share=SHARE+1)),
    ("font_map",data_pdu(0x28,struct.pack("<HHHH",0,0,3,4),source=SERVER+1)),
])
def test_invalid_server_confirmation_rejected(stage, invalid):
    with pytest.raises(RDPFinalizationError):
        check(indicated(invalid),stage)

@pytest.mark.parametrize("stage", STAGES)
def test_wrong_mcs_origin_rejected(stage):
    with pytest.raises(RDPFinalizationError):
        check(indicated(data_pdu(0x28,struct.pack("<HHHH",0,0,3,4)),origin=1003),stage)

