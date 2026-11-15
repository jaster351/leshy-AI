"""Optional interoperability checks with real signed X.509 RSA/ECDSA certificates.

The production binding module has no third-party dependency. These tests
skip when the optional cryptography package is unavailable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from leshy.rdp_credssp_binding import (
    certificate_subject_public_key, prepare_credssp_binding,
)


@pytest.mark.parametrize("key_type", ["rsa", "ec"])
def test_real_x509_subject_key_matches_cryptography(key_type):
    cryptography = pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, ec
    from cryptography.x509.oid import NameOID
    import hashlib

    if key_type == "rsa":
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        expected = private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.PKCS1
        )
    else:
        private = ec.generate_private_key(ec.SECP256R1())
        expected = private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-rdp")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + timedelta(days=1))
            .sign(private, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    assert certificate_subject_public_key(der) == expected
    proof = prepare_credssp_binding(der, hashlib.sha256(der).hexdigest())
    assert len(proof.nonce) == len(proof.client_hash) == len(proof.server_hash) == 32
