"""Двенадцать изолированных сценариев локального CA; пока НЕ ЗАПУСКАТЬ.

Нужно отдельное разрешение владельца. Ключи и сертификаты синтетические,
файлы только во временном каталоге; DPAPI/ACL, TLS runtime, сеть, браузер,
доверие ОС и настоящие потоки не используются. Сборки и приложение не запускаются.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID

from leshy import web_local_ca as ca


class LocalCATests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.brain = Path(self.directory.name)
        self.clock = datetime.now(timezone.utc).replace(microsecond=0)
        self.current = ""
        self.canonical = ""
        self.prepared = []
        self.pending = []
        self.install = Mock(side_effect=self._install)
        self.prepare = Mock(side_effect=self._prepare)
        self._patch("leshy.web_local_ca._now", side_effect=lambda: self.clock)
        self._patch("leshy.web_local_ca.security_root", side_effect=self._security_root)
        self._patch("leshy.web_local_ca.private_path")
        self._patch("leshy.web_transport_settings.private_path")
        self._patch("leshy.web_local_ca.save_secret", side_effect=self._save_secret)
        self._patch("leshy.web_local_ca.load_secret",
                    side_effect=lambda path: path.read_text(encoding="utf-8").removeprefix("dpapi:"))
        self.thread = self._patch("leshy.web_local_ca.threading.Thread",
                                  side_effect=AssertionError("Настоящий поток запрещён"))
        for name in ("connect", "connect_ex", "bind"):
            self._patch(f"socket.socket.{name}", side_effect=AssertionError("Сеть запрещена"))
        self._patch("socket.getaddrinfo", side_effect=AssertionError("DNS запрещён"))
        self.body = {"names": ["192.168.77.12", "fd77::12", "leshy.test"],
                     "auto_renew": True, "confirm": True, "trust_consent": True}
        self.service = self._service()

    def _patch(self, name, **kwargs):
        patcher = patch(name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    @staticmethod
    def _security_root(brain):
        root = Path(brain) / "web-security"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _save_secret(path, value):
        # Подмена защищённого хранилища только для синтетических временных ключей.
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text("dpapi:" + value, encoding="utf-8")
        os.replace(temp, path)

    def _service(self):
        return ca.WebLocalCAService(self.brain, self.prepare, self.install,
                                    lambda: self.current, lambda: self.canonical)

    def _prepare(self, certificate, private):
        chain = x509.load_pem_x509_certificates(certificate.encode("ascii"))
        self.assertEqual(len(chain), 2)
        leaf, root = chain
        leaf.verify_directly_issued_by(root)
        key = serialization.load_pem_private_key(private.encode("ascii"), password=None)
        form, encoding = serialization.PublicFormat.SubjectPublicKeyInfo, serialization.Encoding.DER
        self.assertEqual(key.public_key().public_bytes(encoding, form),
                         leaf.public_key().public_bytes(encoding, form))
        result = SimpleNamespace(leaf=leaf, root=root,
                                 metadata={"fingerprint_sha256": leaf.fingerprint(hashes.SHA256()).hex()})
        self.prepared.append(result)
        return result

    def _install(self, prepared):
        self.pending.append(json.loads(self.service.profile_path.read_text(encoding="utf-8")))
        self.current = prepared.metadata["fingerprint_sha256"]
        return dict(prepared.metadata)

    def _issued(self):
        self.service.issue(self.body)
        return self.service.status()

    def test_unconfigured_constructor_and_start_have_no_side_effects(self):
        with patch.object(ca.ec, "generate_private_key", side_effect=AssertionError("Генерация запрещена")):
            service = self._service()
            service.start()
            service.tick()
            service.stop()
        self.assertEqual(list(self.brain.iterdir()), [])
        self.assertEqual(service.status()["state"], "not_configured")
        self.assertFalse(service.status()["configured"])
        self.thread.assert_not_called()
        self.prepare.assert_not_called()
        self.install.assert_not_called()

    def test_explicit_consent_san_validation_and_canonical_name(self):
        invalid = [[], ["0.0.0.0"], ["::"], ["224.0.0.1"], ["ff02::1"], ["https://leshy.test"],
                   ["leshy.test:8766"], ["*.test"], ["fe80::1%4"], ["[::1]"], [""]]
        for names in invalid:
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.service.issue({**self.body, "names": names})
        for change in ({"trust_consent": False}, {"confirm": 1}, {"auto_renew": 1}, {"extra": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.service.issue({**self.body, **change})
        with self.assertRaises(ValueError):
            ca.normalize_names(["leshy.test"] * 33)
        self.assertEqual(ca.normalize_names(["LESHY.TEST.", "leshy.test", "FD77:0::12"]),
                         ["leshy.test", "fd77::12"])
        self.canonical = "other.test"
        with self.assertRaises(ValueError):
            self.service.issue(self.body)
        self.prepare.assert_not_called()
        self.assertFalse(self.service.authority_path.exists())

    def test_issued_chain_has_private_ip_san_and_public_export_only(self):
        status = self._issued()
        material = self.prepared[-1]
        root, leaf = material.root, material.leaf
        self.assertEqual(root.public_key().curve.name, "secp256r1")
        constraints = root.extensions.get_extension_for_class(x509.BasicConstraints).value
        self.assertTrue(constraints.ca)
        self.assertEqual(constraints.path_length, 0)
        self.assertTrue(root.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign)
        self.assertFalse(leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertEqual(list(leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value),
                         [ExtendedKeyUsageOID.SERVER_AUTH])
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual({str(name.value) for name in san}, set(self.body["names"]))
        self.assertEqual(leaf.not_valid_after_utc - self.clock, timedelta(days=30))
        self.assertEqual(datetime.fromisoformat(status["renew_at"]), self.clock + timedelta(days=23))
        public = self.service.public_certificate()
        self.assertEqual(hashlib.sha256(public).hexdigest(), status["root_fingerprint_sha256"])
        self.assertEqual(x509.load_der_x509_certificate(public).serial_number, root.serial_number)
        self.assertNotIn("PRIVATE", json.dumps(status))
        self.assertNotIn("private_key_pem", status)
        self.assertFalse(status["browser_trust_verified"])
        self.assertFalse(self.pending[0]["enabled"])
        self.assertFalse(self.pending[0]["auto_renew"])
        self.assertTrue(self.pending[0]["attention_required"])
        self.assertEqual(self.pending[0]["certificate_fingerprint_sha256"], self.current)
        self.thread.assert_not_called()

    def test_explicit_reissue_reuses_immutable_root_and_rotates_leaf(self):
        first = self._issued()
        authority = self.service.authority_path.read_bytes()
        previous_serial = self.prepared[-1].leaf.serial_number
        self.service.issue({**self.body, "names": [*self.body["names"], "other.test"]})
        second = self.service.status()
        self.assertEqual(self.service.authority_path.read_bytes(), authority)
        self.assertEqual(second["root_fingerprint_sha256"], first["root_fingerprint_sha256"])
        self.assertNotEqual(second["certificate_fingerprint_sha256"], first["certificate_fingerprint_sha256"])
        self.assertNotEqual(self.prepared[-1].leaf.serial_number, previous_serial)
        self.assertEqual(self._service().public_certificate(), self.service.public_certificate())

    def test_corrupt_missing_or_expired_existing_root_never_regenerates(self):
        issued = self._issued()
        original = self.service.authority_path.read_bytes()
        original_profile = self.service.profile_path.read_bytes()
        self.service.profile_path.write_text("повреждено", encoding="utf-8")
        self.service.authority_path.unlink()
        damaged_profile = self._service()
        with patch.object(ca.ec, "generate_private_key", side_effect=AssertionError("Ротация запрещена")):
            with self.assertRaises(ValueError):
                damaged_profile.issue(self.body)
        self.assertTrue(damaged_profile.status()["attention_required"])
        self.assertFalse(self.service.authority_path.exists())
        with self.assertRaises(RuntimeError):
            damaged_profile.disable({"confirm": True})
        importer = Mock()
        with self.assertRaises(RuntimeError):
            damaged_profile.replace_certificate_manually(importer)
        importer.assert_not_called()
        self.assertEqual(self.service.profile_path.read_text(encoding="utf-8"), "повреждено")
        self.service.profile_path.write_bytes(original_profile)
        # Даже корректный root нельзя читать на Windows из plaintext-хранилища.
        for payload in (original.removeprefix(b"dpapi:"),
                        b"plain:" + original.removeprefix(b"dpapi:")):
            with self.subTest(storage=payload[:6]):
                self.service.authority_path.write_bytes(payload)
                with patch.object(ca, "os", SimpleNamespace(name="nt")):
                    unprotected = self._service()
                    with self.assertRaises(ValueError):
                        unprotected.issue(self.body)
                self.assertTrue(unprotected.status()["attention_required"])
                self.assertEqual(self.service.authority_path.read_bytes(), payload)
        for mode in ("corrupt", "missing", "expired"):
            with self.subTest(mode=mode):
                self.service.authority_path.write_bytes(original)
                self.clock = datetime.fromisoformat(issued["certificate_not_after"]) - timedelta(days=1)
                if mode == "corrupt":
                    self.service.authority_path.write_text("повреждено", encoding="utf-8")
                elif mode == "missing":
                    self.service.authority_path.unlink()
                else:
                    self.clock = datetime.fromisoformat(issued["root_not_after"]) + timedelta(seconds=1)
                restarted = self._service()
                with patch.object(ca.ec, "generate_private_key", side_effect=AssertionError("Ротация запрещена")):
                    with self.assertRaises(ValueError):
                        restarted.issue(self.body)
                    restarted.start()
                self.assertTrue(restarted.status()["attention_required"])
                self.assertFalse(restarted.status()["auto_renew"])
                self.assertEqual(restarted.status()["root_fingerprint_sha256"], issued["root_fingerprint_sha256"])

    def test_prepare_failure_and_changed_certificate_cannot_replace_active_tls(self):
        before = self._issued()
        authority = self.service.authority_path.read_bytes()
        self.prepare.side_effect = ValueError("Учебная ошибка подготовки TLS")
        with self.assertRaises(ValueError):
            self.service.issue(self.body)
        self.assertEqual(self.service.status(), before)
        self.assertEqual(self.service.authority_path.read_bytes(), authority)
        self.assertEqual(self.install.call_count, 1)
        def change_while_preparing(certificate, private):
            prepared = self._prepare(certificate, private)
            self.current = "a" * 64
            return prepared
        self.prepare.side_effect = change_while_preparing
        with self.assertRaises(RuntimeError):
            self.service.issue(self.body)
        self.assertEqual(self.current, "a" * 64)
        self.assertEqual(self.install.call_count, 1)
        self.service.tick()
        self.assertTrue(self.service.status()["attention_required"])

    def test_failed_install_restores_profile_and_keeps_first_root(self):
        self.install.side_effect = OSError("Учебная ошибка записи сертификата")
        with self.assertRaises(RuntimeError):
            self.service.issue(self.body)
        root = self.service.public_certificate()
        self.assertEqual(self.current, "")
        self.assertFalse(self.service.status()["enabled"])
        self.assertEqual(self._service().public_certificate(), root)
        self.install.side_effect = self._install
        first = self._issued()
        self.install.side_effect = OSError("Учебная ошибка записи сертификата")
        with self.assertRaises(RuntimeError):
            self.service.issue(self.body)
        self.assertEqual(self.service.status(), first)
        self.assertEqual(self._service().status(), first)
        self.assertEqual(self.service.public_certificate(), root)

    def test_profile_failures_and_ambiguous_install_pause_without_retry(self):
        first = self._issued()
        root = self.service.public_certificate()
        real_write = ca.atomic_json
        def fail_final(path, value):
            if value["enabled"]:
                raise OSError("Учебная ошибка финальной записи")
            real_write(path, value)
        with patch.object(ca, "atomic_json", side_effect=fail_final):
            with self.assertRaises(RuntimeError):
                self.service.issue(self.body)
        after = self.service.status()
        self.assertNotEqual(self.current, first["certificate_fingerprint_sha256"])
        self.assertTrue(after["attention_required"])
        self.assertFalse(after["auto_renew"])
        restarted = self._service()
        restarted.tick()
        restarted.start()
        self.assertEqual(self.install.call_count, 2)
        self.assertTrue(restarted.status()["attention_required"])
        self.assertEqual(restarted.public_certificate(), root)
        self.service = restarted
        def applied_then_failed(prepared):
            self._install(prepared)
            raise OSError("Учебная потеря подтверждения после применения")
        self.install.side_effect = applied_then_failed
        with self.assertRaises(RuntimeError):
            self.service.issue(self.body)
        self.service.tick()
        self.assertEqual(self.install.call_count, 3)
        self.assertTrue(self.service.status()["attention_required"])
        with patch.object(ca, "atomic_json", side_effect=OSError("Учебная ошибка профиля")):
            with self.assertRaises(RuntimeError):
                self.service.issue(self.body)
        self.assertEqual(self.install.call_count, 3)
        self.assertTrue(self.service.status()["attention_required"])
        self.assertEqual(self.service.public_certificate(), root)

    def test_renewal_deadline_expired_own_leaf_and_restart_mismatch(self):
        first = self._issued()
        root = self.service.public_certificate()
        self.clock += timedelta(days=22)
        self.service.tick()
        self.assertEqual(self.install.call_count, 1)
        self.clock += timedelta(days=9)
        restarted = self._service()
        self.assertTrue(restarted.status()["auto_renew"])
        restarted.tick()
        self.assertEqual(self.install.call_count, 2)
        self.assertNotEqual(self.current, first["certificate_fingerprint_sha256"])
        self.assertEqual(restarted.public_certificate(), root)
        self.current = "b" * 64
        foreign = self._service()
        foreign.tick()
        foreign.start()
        self.assertFalse(foreign.status()["auto_renew"])
        self.assertTrue(foreign.status()["attention_required"])
        self.assertEqual(self.current, "b" * 64)
        self.assertEqual(self.install.call_count, 2)

    def test_canonical_change_and_cancellation_prevent_stale_install(self):
        self._issued()
        self.canonical = "changed.test"
        self.service.tick()
        self.assertTrue(self.service.status()["attention_required"])
        self.assertEqual(self.install.call_count, 1)
        self.canonical = ""
        def stop_while_preparing(certificate, private):
            prepared = self._prepare(certificate, private)
            self.service.stop()
            return prepared
        self.prepare.side_effect = stop_while_preparing
        with self.assertRaises(RuntimeError):
            self.service.issue(self.body)
        self.assertEqual(self.install.call_count, 1)

    def test_manual_import_disables_renewal_and_failure_restores_previous_scope(self):
        first = self._issued()
        with self.assertRaises(OSError):
            self.service.replace_certificate_manually(Mock(side_effect=OSError("Учебный отказ импорта")))
        self.assertEqual(self.service.status(), first)
        self.assertEqual(self._service().status(), first)
        def successful_import():
            persisted = json.loads(self.service.profile_path.read_text(encoding="utf-8"))
            self.assertFalse(persisted["enabled"])
            self.assertFalse(persisted["auto_renew"])
            self.current = "c" * 64
            return {"fingerprint_sha256": self.current}
        self.service.replace_certificate_manually(successful_import)
        self.assertEqual(self.service.status()["state"], "disabled")
        self.clock += timedelta(days=31)
        self.service.tick()
        self.assertEqual(self.install.call_count, 1)
        self.assertEqual(self.current, "c" * 64)

    def test_disable_requires_boolean_consent_and_worker_is_explicit_and_joined(self):
        self._issued()
        for body in ({"confirm": 1}, {"confirm": True, "extra": True}, {}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.service.disable(body)
        worker = Mock()
        worker.is_alive.return_value = True
        with patch.object(ca.threading, "Thread", return_value=worker) as factory:
            self.service.start()
            self.service.start()
            self.assertEqual(factory.call_count, 1)
            worker.start.assert_called_once()
            def joined_outside_lock(**kwargs):
                self.assertFalse(self.service._lock._is_owned())
            worker.join.side_effect = joined_outside_lock
            self.service.stop()
            worker.join.assert_called_once_with(timeout=5)
        self.service.disable({"confirm": True})
        self.assertEqual(self._service().status()["state"], "disabled")
        self.thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
