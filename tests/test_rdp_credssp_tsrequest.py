"""MS-CSSP v5/v6 TSRequest DER and fail-closed parsing."""
from __future__ import annotations

import pytest
from leshy.rdp_credssp_tsrequest import (
    TSRequest, TSRequestError, encode_tsrequest, decode_tsrequest,
)


@pytest.mark.parametrize("packet", [
    TSRequest(), TSRequest(version=5),
    TSRequest(nego_tokens=(b"gss-token",)),
    TSRequest(version=5, nego_tokens=(b"a", b"b"), client_nonce=b"n" * 32),
    TSRequest(pub_key_auth=b"encrypted-binding", client_nonce=b"x" * 32),
    TSRequest(error_code=0xC000006D),
])
def test_roundtrip(packet):
    assert decode_tsrequest(encode_tsrequest(packet)) == packet


def test_known_der_for_v6_and_nego_data():
    assert encode_tsrequest(TSRequest()) == bytes.fromhex("3005a003020106")
    assert encode_tsrequest(TSRequest(nego_tokens=(b"abc",))) == bytes.fromhex(
        "3012a003020106a10b30093007a0050403616263"
    )


@pytest.mark.parametrize("payload", [
    b"", b"\x30\x80\x00\x00", b"\x30\x81\x05\xa0\x03\x02\x01\x06",
    b"\x30\x05\xa0\x03\x02\x01\x04", # legacy v4
    b"\x30\x07\xa0\x05\x02\x03\x00\x00\x06", # noncanonical int
    b"\x30\x07\xa0\x03\x02\x01\x06\xff\x00", # unknown field
    b"\x30\x0a\xa0\x03\x02\x01\x06\xa0\x03\x02\x01\x06", # duplicate version
    b"\x30\x09\xa0\x03\x02\x01\x06\xa2\x02\x04\x00", # authInfo forbidden
    bytes.fromhex("3005a003020106") + b"\x00", # trailing
])
def test_malformed_rejected(payload):
    with pytest.raises(TSRequestError):
        decode_tsrequest(payload)


def test_secret_field_cannot_be_constructed():
    with pytest.raises(TypeError):
        TSRequest(auth_info=b"password")
    with pytest.raises(TSRequestError):
        encode_tsrequest(TSRequest(nego_tokens=(b"x" * 200000,)))
    with pytest.raises(TSRequestError):
        decode_tsrequest(b"\x30" + b"a" * 300000)


def test_invalid_nonce_and_bool_integer():
    with pytest.raises(TSRequestError):
        encode_tsrequest(TSRequest(client_nonce=b"123"))
    with pytest.raises(TSRequestError):
        encode_tsrequest(TSRequest(error_code=True))
