"""CredSSP v5/v6 public-key binding vectors and X.509 parsing tests."""
from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
import pytest

from leshy.rdp_credssp_binding import (
    CredSSPBinding, RDPBindingError, certificate_subject_public_key,
    client_to_server_hash, server_to_client_hash, prepare_credssp_binding,
)


def tlv(tag, content):
    n = len(content)
    prefix = bytes((n,)) if n < 128 else b"\x81" + bytes((n,))
    return bytes((tag,)) + prefix + content


KEY = b"\x30\x03\x02\x01\x01"


def certificate(key=KEY, *, unused=0, with_version=True):
    # Minimal DER envelope sufficient for testing location of SubjectPublicKey.
    # These synthetic certificate fields do NOT constitute a valid signed
    # certificate. Production pinning must use actual peer certificate bytes.
    alg = tlv(0x30, tlv(0x06, b"\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01"))
    issuer = tlv(0x30, b"\x31\x00")
    validity = tlv(0x30, b"\x17\x0d200101000000Z")
    subject = tlv(0x30, b"\x31\x00")
    spki = tlv(0x30, alg + tlv(0x03, bytes((unused,)) + key))
    version = tlv(0xA0, tlv(0x02, b"\x02")) if with_version else b""
    tbs = tlv(0x30, version + tlv(0x02, b"\x01") + alg + issuer + validity + subject + spki)
    return tlv(0x30, tbs + alg + tlv(0x03, b"\x00\xff"))


def test_subject_public_key_not_full_cert_or_spki():
    cert = certificate()
    extracted = certificate_subject_public_key(cert)
    assert extracted == KEY
    assert extracted != cert
    assert certificate_subject_public_key(certificate(with_version=False)) == KEY


def test_binding_hashes_independent_known_vectors():
    nonce = bytes(range(32))
    assert client_to_server_hash(nonce, KEY).hex() == (
        "98e31c83ebc847c8cd1159817246035405e1680e486b6293198e42485513c95d"
    )
    assert server_to_client_hash(nonce, KEY).hex() == (
        "cb3c8da5dcd91357b311a3b0d3c0953727677ff756a4d505f789758407bf95e4"
    )
    assert client_to_server_hash(nonce, KEY) != server_to_client_hash(nonce, KEY)


def test_pin_aware_fresh_nonce_and_server_proof(monkeypatch):
    cert = certificate()
    pin = hashlib.sha256(cert).hexdigest()
    nonce = bytes(range(32))
    monkeypatch.setattr("leshy.rdp_credssp_binding.secrets.token_bytes", lambda n: nonce)
    context = prepare_credssp_binding(cert, pin.upper())
    assert context.nonce == nonce
    assert context.client_hash == client_to_server_hash(nonce, KEY)
    context.verify_unsealed_server_hash(server_to_client_hash(nonce, KEY))
    with pytest.raises(PermissionError):
        context.verify_unsealed_server_hash(context.client_hash)
    with pytest.raises(PermissionError):
        context.verify_unsealed_server_hash(b"")
    with pytest.raises(PermissionError):
        context.verify_unsealed_server_hash(b"t" * 32)
    with pytest.raises(FrozenInstanceError):
        context.nonce = b"\x00" * 32


def test_untrusted_certificate_and_bad_pin_rejected(monkeypatch):
    cert = certificate()
    pin = hashlib.sha256(cert).hexdigest()
    with pytest.raises(PermissionError):
        prepare_credssp_binding(cert, "f" * 64 if pin != "f" * 64 else "a" * 64)
    with pytest.raises(RDPBindingError):
        prepare_credssp_binding(cert, "not-a-pin")
    with pytest.raises(PermissionError):
        prepare_credssp_binding(cert + b"\x00", pin)


@pytest.mark.parametrize("input_cert", [
    b"", b"\x30\x80\x00\x00",
    b"\x30\x81\x05\x01\x02\x03\x04\x05",
    certificate()[:-1],
    certificate() + b"\x00",
    certificate(unused=1),
    certificate(key=b""),
])
def test_malformed_certificates_fail(input_cert):
    with pytest.raises(RDPBindingError):
        certificate_subject_public_key(input_cert)


@pytest.mark.parametrize("nonce, public_key", [
    (b"", KEY), (b"n" * 31, KEY), (b"n" * 33, KEY),
    (b"n" * 32, b""), (b"n" * 32, b"a" * 17000),
])
def test_nonce_and_public_key_bounds(nonce, public_key):
    with pytest.raises(RDPBindingError):
        client_to_server_hash(nonce, public_key)
