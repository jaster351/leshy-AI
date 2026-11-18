"""Изолированные проверки сетевых настроек и импорта; без настоящего сервера/ACME."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from leshy.web_certificates import WebCertificateStore, inspect_certificate
from leshy.web_transport_settings import WebTransportSettings, browser_url, validate_settings


def material(*, names=None, days=10, issuer_key=None, issuer_name=None, ca=False):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.invalid")])
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name or subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(days=days))
                   .add_extension(x509.SubjectAlternativeName(names or [x509.DNSName("test.invalid")]), False)
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
                   .sign(issuer_key or key, hashes.SHA256()))
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    secret = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode("ascii")
    return certificate, key, pem, secret


class WebTransportSettingsTests(unittest.TestCase):
    def test_defaults_and_independent_ports(self):
        value = validate_settings({})
        self.assertEqual((value["http_port"], value["https_port"]), (8765, 8766))
        for changes in ({"http_port": True}, {"https_port": 0}, {"http_port": 65536},
                        {"https_port": 8765}, {"http_enabled": False, "https_enabled": False},
                        {"http_enabled": False, "hostname": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_settings(changes)

    def test_saved_port_is_used_unless_explicitly_overridden(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            store = WebTransportSettings(temp)
            store.save({"http_port": 9080, "https_port": 9443, "hostname": "test.invalid", "confirm": True})
            self.assertEqual(store.effective()["http_port"], 9080)
            self.assertEqual(store.effective(9081)["http_port"], 9081)
            with patch.dict(os.environ, {"LESHY_PORT": "9082"}):
                self.assertEqual(store.effective()["http_port"], 9082)
                self.assertEqual(store.effective(9083)["http_port"], 9083)
            store.save({"http_enabled": False, "confirm": True})
            self.assertEqual(browser_url(temp), "https://test.invalid:9443/")


class CertificateImportTests(unittest.TestCase):
    def test_dns_san_cannot_substitute_for_ip_san(self):
        _, _, pem, secret = material(names=[x509.DNSName("127.0.0.1")])
        with self.assertRaises(ValueError):
            inspect_certificate(pem, secret, "127.0.0.1")
        _, _, pem, secret = material(names=[x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])
        metadata, _ = inspect_certificate(pem, secret, "127.0.0.1")
        self.assertEqual(metadata["ip_addresses"], ["127.0.0.1"])
        self.assertFalse(metadata["browser_trust_verified"])

    def test_mismatched_key_rejected(self):
        _, _, pem, _ = material()
        _, _, _, other_key = material()
        with self.assertRaises(ValueError):
            inspect_certificate(pem, other_key)

    def test_chain_expiry_bounds_context_lifetime(self):
        issuer, issuer_key, issuer_pem, _ = material(days=1, ca=True)
        _, _, pem, secret = material(days=10, issuer_key=issuer_key, issuer_name=issuer.subject)
        metadata, _ = inspect_certificate(pem + issuer_pem, secret, "test.invalid")
        self.assertEqual(metadata["valid_until"], issuer.not_valid_after_utc.isoformat())

    def test_end_entity_cannot_sign_intermediate_chain(self):
        issuer, issuer_key, issuer_pem, _ = material(ca=False)
        _, _, pem, secret = material(issuer_key=issuer_key, issuer_name=issuer.subject)
        with self.assertRaises(ValueError):
            inspect_certificate(pem + issuer_pem, secret, "test.invalid")

    def test_failed_persistence_keeps_prior_certificate_and_context(self):
        with TemporaryDirectory() as temp:
            store = WebCertificateStore(temp, lambda: "test.invalid")
            _, _, pem, secret = material()
            before = store.install(pem, secret)
            raw = store.path.read_bytes()
            context = store.context()
            _, _, newer, newer_key = material()
            with patch("leshy.web_certificates.save_secret", side_effect=OSError("Имитация отказа записи")):
                with self.assertRaises(OSError):
                    store.install(newer, newer_key)
            self.assertEqual(store.path.read_bytes(), raw)
            self.assertEqual(store.status()["fingerprint_sha256"], before["fingerprint_sha256"])
            self.assertIs(store.context(), context)
            self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
            self.assertFalse(list(Path(temp).rglob(".tls-*.pem")))


if __name__ == "__main__":
    unittest.main()
