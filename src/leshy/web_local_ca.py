"""Локальный CA для HTTPS по IP; доверие на устройствах задаёт владелец."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
from typing import Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .secret_store import load_secret, save_secret
from .web_transport_settings import atomic_json, hostname, private_path, reject_link, security_root


_LEAF_LIFETIME = timedelta(days=30)
_RENEW_BEFORE = timedelta(days=7)
_ROOT_LIFETIME = timedelta(days=3650)
_HEX = re.compile(r"[0-9a-f]{64}")
_PENDING = "Установка сертификата не завершена; проверьте состояние перед повторным выпуском"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_names(value: object) -> list[str]:
    """Нормализовать явные SAN без URL, wildcard и адресов общей привязки."""
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("Укажите от 1 до 32 имён или IP сервера")
    names = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 253 or any(c in raw for c in "%*[]/\\"):
            raise ValueError("Укажите обычные имена или IP без схемы, порта, wildcard и зоны IPv6")
        name = hostname(raw)
        try:
            address = ipaddress.ip_address(name)
        except ValueError:
            address = None
        if address is not None and (address.is_unspecified or address.is_multicast):
            raise ValueError("Адрес общей привязки или multicast не является адресом HTTPS-сервера")
        if name not in names:
            names.append(name)
    return names


def _blank() -> dict:
    return {"schema_version": 1, "enabled": False, "auto_renew": False, "names": [],
            "state": "not_configured", "root_fingerprint_sha256": "", "root_not_after": "",
            "certificate_fingerprint_sha256": "", "certificate_not_after": "", "renew_at": "",
            "attention_required": False, "last_error": ""}


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Срок сертификата должен содержать часовой пояс")
    return result.astimezone(timezone.utc)


def _fingerprint(certificate: x509.Certificate) -> str:
    return certificate.fingerprint(hashes.SHA256()).hex()


class WebLocalCAService:
    """Порядок блокировок: внешний ACME → этот CA → callback транспорта.

    Корневой ключ никогда не возвращается публичными методами. Промежуточный профиль
    записывается до сертификата: после сбоя или неизвестного результата запуск
    не возобновляет выпуск без нового явного действия владельца.
    """

    def __init__(self, brain_dir: Path | str, prepare_certificate: Callable,
                 install_prepared: Callable, installed_fingerprint: Callable[[], str],
                 hostname_provider: Callable[[], str]):
        # Чтение отключённой функции не создаёт файлов, ключей и потоков.
        self.root = Path(brain_dir) / "web-security"
        self.authority_path = self.root / "local-ca-authority.secret"
        self.profile_path = self.root / "local-ca.json"
        self._prepare = prepare_certificate
        self._install = install_prepared
        self._installed_fingerprint = installed_fingerprint
        self._hostname = hostname_provider
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = None
        self._generation = 0
        self._authority = None
        self._authority_error = ""
        self._profile_error = False
        self._profile = _blank()
        try:
            self._profile = self._read_profile()
        except Exception:
            self._profile_error = True
            self._authority_error = "Профиль локального CA повреждён; восстановите исходный профиль и CA"
            self._attention(self._authority_error, persist=False)
        try:
            self._authority = self._read_authority()
        except Exception:
            self._authority_error = "Корневой сертификат или ключ недоступен; восстановите исходный CA"
            self._attention(self._authority_error, persist=False)
        self._reconcile()

    def _read_profile(self) -> dict:
        reject_link(self.root)
        reject_link(self.profile_path)
        if not self.profile_path.exists():
            return _blank()
        if self.profile_path.stat().st_size > 64 * 1024:
            raise ValueError("Профиль локального CA превышает допустимый размер")
        value = json.loads(self.profile_path.read_text(encoding="utf-8"))
        fields = _blank()
        if (not isinstance(value, dict) or set(value) != set(fields)
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Неподдерживаемый профиль локального CA")
        if any(type(value[key]) is not bool for key in ("enabled", "auto_renew", "attention_required")):
            raise ValueError("Повреждены параметры продления локального CA")
        if value["state"] not in {"not_configured", "issued", "disabled", "attention"}:
            raise ValueError("Неизвестное состояние локального CA")
        if not isinstance(value["names"], list):
            raise ValueError("Повреждена область локального сертификата")
        if value["names"]:
            value["names"] = normalize_names(value["names"])
        elif value["enabled"]:
            raise ValueError("Нет области локального сертификата")
        for key in ("root_fingerprint_sha256", "certificate_fingerprint_sha256"):
            if not isinstance(value[key], str) or (value[key] and not _HEX.fullmatch(value[key])):
                raise ValueError("Повреждён отпечаток сертификата")
        for key in ("root_not_after", "certificate_not_after", "renew_at"):
            if not isinstance(value[key], str):
                raise ValueError("Повреждён срок сертификата")
            if value[key]:
                _date(value[key])
        if not isinstance(value["last_error"], str) or len(value["last_error"]) > 1000:
            raise ValueError("Повреждён статус локального CA")
        if value["enabled"] and (value["state"] != "issued" or value["attention_required"]
                                 or not value["root_fingerprint_sha256"]
                                 or not value["certificate_fingerprint_sha256"] or not value["renew_at"]):
            raise ValueError("Профиль выпуска не завершён")
        return value

    def _read_authority(self):
        reject_link(self.root)
        reject_link(self.authority_path)
        if not self.authority_path.exists():
            if self._profile["root_fingerprint_sha256"] or self._authority is not None:
                raise ValueError("Исходный CA отсутствует; новый автоматически не создаётся")
            return None
        if self.authority_path.stat().st_size > 64 * 1024:
            raise ValueError("Хранилище CA превышает допустимый размер")
        if os.name == "nt":
            with self.authority_path.open("rb") as stream:
                if stream.read(6) != b"dpapi:":
                    raise ValueError("Корневой ключ локального CA должен быть защищён Windows DPAPI")
        value = json.loads(load_secret(self.authority_path))
        if not isinstance(value, dict) or set(value) != {"certificate_pem", "private_key_pem"}:
            raise ValueError("Повреждено хранилище CA")
        certificate = x509.load_pem_x509_certificate(value["certificate_pem"].encode("ascii"))
        key = serialization.load_pem_private_key(value["private_key_pem"].encode("ascii"), password=None)
        if (not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1"
                or not isinstance(certificate.signature_hash_algorithm, hashes.SHA256)):
            raise ValueError("Неподдерживаемый ключ локального CA")
        encoding, form = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if key.public_key().public_bytes(encoding, form) != certificate.public_key().public_bytes(encoding, form):
            raise ValueError("Ключ не соответствует корневому сертификату")
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        if (certificate.subject != certificate.issuer or not constraints.ca
                or constraints.path_length != 0 or not usage.key_cert_sign):
            raise ValueError("Сертификат не является локальным корневым CA")
        certificate.verify_directly_issued_by(certificate)
        expected = self._profile["root_fingerprint_sha256"]
        if expected and _fingerprint(certificate) != expected:
            raise ValueError("Корневой CA изменился; автоматическая замена запрещена")
        if self._authority is not None and _fingerprint(self._authority[0]) != _fingerprint(certificate):
            raise ValueError("Корневой CA изменился после запуска")
        return certificate, key

    @staticmethod
    def _valid_authority(authority) -> None:
        certificate = authority[0]
        if certificate.not_valid_before_utc > _now() or certificate.not_valid_after_utc <= _now():
            raise ValueError("Корневой CA ещё не действует или просрочен; автоматическая ротация запрещена")

    def _reconcile(self) -> None:
        if self._authority is not None:
            try:
                self._valid_authority(self._authority)
            except ValueError:
                self._attention("Корневой CA просрочен или ещё не действует; требуется проверка владельцем", persist=False)
        if self._profile["enabled"]:
            try:
                if (self._authority is None or self._profile["certificate_fingerprint_sha256"]
                        != self._installed_fingerprint()):
                    raise ValueError
            except Exception:
                self._attention("Установленный сертификат изменён или недоступен; автопродление остановлено", persist=False)

    def _ensure_root(self) -> None:
        actual = security_root(self.root.parent)
        if actual != self.root:
            raise ValueError("Изменился каталог локального CA")

    def _save_profile(self) -> None:
        if self._profile_error:
            raise ValueError("Повреждённый профиль CA нельзя заменить без восстановления исходного состояния")
        self._ensure_root()
        atomic_json(self.profile_path, self._profile)

    def _attention(self, message: str, *, persist=True) -> None:
        self._profile.update(enabled=False, auto_renew=False, state="attention",
                             attention_required=True, last_error=message)
        if persist:
            try:
                self._save_profile()
            except Exception:
                # Предыдущая запись перед установкой уже запрещает автопродление.
                self._profile["last_error"] = "Не удалось сохранить состояние CA; требуется проверка владельцем"

    def _guard(self, generation: int) -> None:
        if generation != self._generation or self._stop.is_set():
            raise RuntimeError("Выпуск локального сертификата отменён")

    def _scope(self, names: list[str]) -> None:
        canonical = hostname(self._hostname())
        if canonical and canonical not in names:
            raise ValueError("Добавьте основное имя HTTPS-сервера в список SAN локального сертификата")

    @staticmethod
    def _new_authority():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Леший"),
                          x509.NameAttribute(NameOID.COMMON_NAME, "Леший: локальный центр сертификации")])
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(_now() - timedelta(minutes=5)).not_valid_after(_now() + _ROOT_LIFETIME)
                       .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                       .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                                   key_encipherment=False, data_encipherment=False,
                                                   key_agreement=False, key_cert_sign=True, crl_sign=True,
                                                   encipher_only=False, decipher_only=False), critical=True)
                       .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                       .sign(key, hashes.SHA256()))
        return certificate, key

    def _persist_new_authority(self, authority) -> None:
        self._ensure_root()
        reject_link(self.authority_path)
        reject_link(self.authority_path.with_suffix(self.authority_path.suffix + ".tmp"))
        # Исключительное создание не позволяет двум процессам заменить друг другу CA.
        fd = os.open(self.authority_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        identity = os.fstat(fd).st_ino
        os.close(fd)
        try:
            certificate, key = authority
            value = {"certificate_pem": certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"),
                     "private_key_pem": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()).decode("ascii")}
            save_secret(self.authority_path, json.dumps(value, ensure_ascii=True))
            private_path(self.authority_path)
        except Exception:
            # Удаляется только собственный пустой placeholder, не сохранённый CA.
            try:
                current = self.authority_path.stat()
                if current.st_ino == identity and current.st_size == 0:
                    self.authority_path.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _new_leaf(authority, names: list[str]):
        root, root_key = authority
        if root.not_valid_after_utc <= _now() + _LEAF_LIFETIME:
            raise ValueError("Срок корневого CA слишком короток для продления; настройте замену доверия вручную")
        key = ec.generate_private_key(ec.SECP256R1())
        san = []
        for name in names:
            try:
                san.append(x509.IPAddress(ipaddress.ip_address(name)))
            except ValueError:
                san.append(x509.DNSName(name))
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Леший: сервер HTTPS")])
        leaf = (x509.CertificateBuilder().subject_name(subject).issuer_name(root.subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(max(root.not_valid_before_utc, _now() - timedelta(minutes=5)))
                .not_valid_after(_now() + _LEAF_LIFETIME)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName(san), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                            key_encipherment=False, data_encipherment=False,
                                            key_agreement=False, key_cert_sign=False, crl_sign=False,
                                            encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
                .sign(root_key, hashes.SHA256()))
        chain = leaf.public_bytes(serialization.Encoding.PEM) + root.public_bytes(serialization.Encoding.PEM)
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        return leaf, chain.decode("ascii"), private.decode("ascii")

    def _replace(self, names: list[str], auto_renew: bool, expected_fingerprint: str | None = None) -> None:
        self._generation += 1
        generation = self._generation
        self._guard(generation)
        self._scope(names)
        if self._authority_error:
            raise ValueError(self._authority_error)
        try:
            authority = self._read_authority()
        except Exception:
            raise ValueError("Исходный локальный CA недоступен или изменён; автоматическая замена запрещена") from None
        old_installed = self._installed_fingerprint()
        if expected_fingerprint is not None and old_installed != expected_fingerprint:
            raise ValueError("Установленный сертификат изменён; локальное продление остановлено")
        fresh = authority is None
        if fresh:
            authority = self._new_authority()
        self._valid_authority(authority)
        leaf, chain, private = self._new_leaf(authority, names)
        prepared = self._prepare(chain, private)
        self._guard(generation)
        if self._installed_fingerprint() != old_installed:
            raise RuntimeError("Сертификат изменился во время подготовки; повторите действие после проверки")
        old_profile = {**self._profile, "names": list(self._profile["names"])}
        if fresh:
            self._persist_new_authority(authority)
        self._authority = authority
        if not old_profile["root_fingerprint_sha256"]:
            # Даже неудачный первый выпуск сохраняет личность уже созданного CA.
            old_profile.update(root_fingerprint_sha256=_fingerprint(authority[0]),
                               root_not_after=authority[0].not_valid_after_utc.isoformat())
        next_profile = {**_blank(), "enabled": True, "auto_renew": auto_renew, "names": names,
                        "state": "issued", "root_fingerprint_sha256": _fingerprint(authority[0]),
                        "root_not_after": authority[0].not_valid_after_utc.isoformat(),
                        "certificate_fingerprint_sha256": _fingerprint(leaf),
                        "certificate_not_after": leaf.not_valid_after_utc.isoformat(),
                        "renew_at": (leaf.not_valid_after_utc - _RENEW_BEFORE).isoformat()}
        self._profile = {**next_profile, "enabled": False, "auto_renew": False, "state": "attention",
                         "attention_required": True, "last_error": _PENDING}
        try:
            self._save_profile()
        except Exception:
            self._attention("Не удалось подготовить профиль CA; прежний сертификат сохранён")
            raise RuntimeError("Не удалось подготовить профиль CA; прежний сертификат сохранён") from None
        try:
            self._guard(generation)
            if self._installed_fingerprint() != old_installed:
                raise RuntimeError("Сертификат изменился во время установки")
            self._install(prepared)
            self._guard(generation)
            if self._installed_fingerprint() != next_profile["certificate_fingerprint_sha256"]:
                raise RuntimeError("Установка локального сертификата не подтверждена")
            self._profile = next_profile
            self._save_profile()
        except Exception:
            try:
                current = self._installed_fingerprint()
            except Exception:
                current = None
            if current == old_installed:
                self._profile = old_profile
                try:
                    self._save_profile()
                except Exception:
                    self._attention("Не удалось восстановить профиль продления; требуется проверка владельцем")
            else:
                self._attention(_PENDING)
            raise RuntimeError("Установка локального сертификата не завершена; проверьте сертификат и автопродление") from None

    def issue(self, body: dict) -> dict:
        if (not isinstance(body, dict) or set(body) != {"names", "auto_renew", "confirm", "trust_consent"}
                or body.get("confirm") is not True or body.get("trust_consent") is not True
                or type(body.get("auto_renew")) is not bool):
            raise ValueError("Подтвердите имена сервера, выпуск локального CA и настройку доверия на устройствах")
        names = normalize_names(body["names"])
        with self._lock:
            self._replace(names, body["auto_renew"])
            return self.status()

    def disable(self, body: dict) -> dict:
        if not isinstance(body, dict) or set(body) != {"confirm"} or body.get("confirm") is not True:
            raise ValueError("Подтвердите отключение автопродления локального CA")
        with self._lock:
            self._generation += 1
            if self._profile["state"] != "not_configured":
                self._profile.update(enabled=False, auto_renew=False, state="disabled")
                try:
                    self._save_profile()
                except Exception:
                    self._attention("Не удалось сохранить отключение локального CA; требуется проверка владельцем")
                    raise RuntimeError("Отключение локального CA не подтверждено") from None
            return self.status()

    def replace_certificate_manually(self, installer: Callable[[], dict]) -> dict:
        with self._lock:
            self._generation += 1
            if self._profile["state"] == "not_configured":
                return installer()
            previous = {**self._profile, "names": list(self._profile["names"])}
            before = self._installed_fingerprint()
            self._profile.update(enabled=False, auto_renew=False, state="disabled")
            try:
                self._save_profile()
            except Exception:
                self._attention("Не удалось остановить локальное продление перед импортом")
                raise RuntimeError("Импорт не начат: отключение локального продления не подтверждено") from None
            try:
                return installer()
            except Exception:
                try:
                    same = self._installed_fingerprint() == before
                except Exception:
                    same = False
                if same:
                    self._profile = previous
                    try:
                        self._save_profile()
                    except Exception:
                        self._attention("Не удалось восстановить локальное продление после ошибки импорта")
                else:
                    self._attention("Импорт мог примениться; локальное продление остановлено")
                raise

    def public_certificate(self) -> bytes:
        with self._lock:
            try:
                authority = self._read_authority()
            except Exception:
                raise ValueError("Исходный корневой сертификат недоступен или повреждён") from None
            if authority is None:
                raise ValueError("Локальный корневой сертификат ещё не создан")
            self._valid_authority(authority)
            return authority[0].public_bytes(serialization.Encoding.DER)

    def status(self) -> dict:
        with self._lock:
            value = {**self._profile, "names": list(self._profile["names"]),
                     "configured": bool(self._authority is not None or self.authority_path.exists()
                                        or self._profile["root_fingerprint_sha256"]),
                     "browser_trust_verified": False}
            value.pop("schema_version", None)
            if self._authority is not None:
                value.update(root_fingerprint_sha256=_fingerprint(self._authority[0]),
                             root_not_after=self._authority[0].not_valid_after_utc.isoformat())
            return value

    def tick(self) -> dict:
        with self._lock:
            if (self._stop.is_set() or not self._profile["enabled"] or not self._profile["auto_renew"]
                    or self._profile["attention_required"]):
                return self.status()
            try:
                if self._installed_fingerprint() != self._profile["certificate_fingerprint_sha256"]:
                    raise ValueError("Установленный сертификат изменён; локальное продление остановлено")
                self._scope(self._profile["names"])
                authority = self._read_authority()
                if authority is None:
                    raise ValueError("Исходный CA отсутствует")
                self._valid_authority(authority)
                if _now() < _date(self._profile["renew_at"]):
                    return self.status()
                self._replace(list(self._profile["names"]), True,
                              self._profile["certificate_fingerprint_sha256"])
            except Exception:
                self._attention("Автопродление локального сертификата остановлено; требуется проверка владельцем")
            return self.status()

    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._reconcile()
            if not self._profile["enabled"] or not self._profile["auto_renew"] or self._profile["attention_required"]:
                return
            self._stop.clear()
            worker = threading.Thread(target=self._renew_loop, name="leshy-local-ca-renew", daemon=True)
            self._worker = worker
            try:
                worker.start()
            except Exception:
                self._worker = None
                self._attention("Не удалось запустить локальное автопродление")
                raise RuntimeError("Автопродление локального сертификата не запущено") from None

    def _renew_loop(self) -> None:
        while not self._stop.wait(60):
            self.tick()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._generation += 1
            worker = self._worker
        if worker is not None and worker is not threading.current_thread() and worker.is_alive():
            worker.join(timeout=5)


__all__ = ["WebLocalCAService", "normalize_names"]
