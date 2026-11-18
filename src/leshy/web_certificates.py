"""Проверка и атомарная замена сертификата без выдачи закрытого ключа API."""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
from typing import Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from .secret_store import load_secret, save_secret
from .web_transport_settings import private_path, reject_link, security_root


def _matches_host(name: str, expected: str) -> bool:
    left, right = name.lower().rstrip("."), expected.lower().rstrip(".")
    if left.startswith("*."):
        return right.count(".") == left.count(".") and right.endswith(left[1:])
    return left == right


def covers_hostname(metadata: dict, expected: str) -> bool:
    if not expected:
        return True
    try:
        target = ipaddress.ip_address(expected)
    except ValueError:
        return any(_matches_host(name, expected) for name in metadata.get("dns_names", []))
    return any(ipaddress.ip_address(name) == target for name in metadata.get("ip_addresses", []))


def inspect_certificate(certificate_pem: str, private_key_pem: str,
                        expected_hostname: str = "", password: str | None = None) -> tuple[dict, str]:
    """Проверить материал локально; возвращаемый ключ остаётся только в памяти."""
    if not isinstance(certificate_pem, str) or not isinstance(private_key_pem, str):
        raise ValueError("Сертификат и ключ должны быть PEM-текстом")
    if len(certificate_pem.encode("utf-8")) > 256 * 1024 or len(private_key_pem.encode("utf-8")) > 64 * 1024:
        raise ValueError("PEM-файл превышает допустимый размер")
    if password is not None and (not isinstance(password, str) or len(password) > 1024):
        raise ValueError("Некорректный пароль закрытого ключа")
    try:
        chain = x509.load_pem_x509_certificates(certificate_pem.encode("utf-8"))
        if not chain or len(chain) > 10:
            raise ValueError
        key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"),
                                                 password=password.encode("utf-8") if password else None)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Не удалось прочитать PEM-сертификат или закрытый ключ; проверьте формат и пароль") from exc
    if not ((isinstance(key, rsa.RSAPrivateKey) and key.key_size >= 2048)
            or (isinstance(key, ec.EllipticCurvePrivateKey) and key.key_size >= 256)):
        raise ValueError("HTTPS требует RSA не менее 2048 бит или EC не менее 256 бит")
    leaf = chain[0]
    public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if leaf.public_key().public_bytes(*public_format) != key.public_key().public_bytes(*public_format):
        raise ValueError("Закрытый ключ не соответствует сертификату")
    now = datetime.now(timezone.utc)
    for certificate in chain:
        if certificate.not_valid_before_utc > now or certificate.not_valid_after_utc <= now:
            raise ValueError("Сертификат или его цепочка ещё не действуют либо уже просрочены")
    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = list(san.get_values_for_type(x509.DNSName))
        ip_names = [str(value) for value in san.get_values_for_type(x509.IPAddress)]
        names = dns_names + ip_names
    except x509.ExtensionNotFound as exc:
        raise ValueError("Сертификат должен содержать SAN с именем сервера или IP") from exc
    if not names or len(names) > 100:
        raise ValueError("Некорректный список SAN сертификата")
    if not covers_hostname({"dns_names": dns_names, "ip_addresses": ip_names}, expected_hostname):
        raise ValueError("Имя сервера отсутствует в SAN сертификата")
    try:
        usages = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.SERVER_AUTH not in usages and x509.ObjectIdentifier("2.5.29.37.0") not in usages:
            raise ValueError("Сертификат не разрешает TLS-аутентификацию сервера")
    except x509.ExtensionNotFound:
        pass
    for position, issuer in enumerate(chain[1:], start=1):
        try:
            constraints = issuer.extensions.get_extension_for_class(x509.BasicConstraints).value
        except x509.ExtensionNotFound as exc:
            raise ValueError("Промежуточный сертификат должен содержать BasicConstraints CA") from exc
        if not constraints.ca:
            raise ValueError("Сертификат издателя не разрешает выпуск других сертификатов")
        try:
            usage = issuer.extensions.get_extension_for_class(x509.KeyUsage).value
            if not usage.key_cert_sign:
                raise ValueError("Ключ издателя не разрешает подпись сертификатов")
        except x509.ExtensionNotFound:
            pass
        subordinate = sum(item.subject != item.issuer for item in chain[1:position])
        if constraints.path_length is not None and subordinate > constraints.path_length:
            raise ValueError("Превышена допустимая длина цепочки издателя")
    for certificate, issuer in zip(chain, chain[1:]):
        try:
            certificate.verify_directly_issued_by(issuer)
        except Exception as exc:
            raise ValueError("Нарушены порядок или подписи цепочки сертификатов") from exc
    normalized = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()).decode("ascii")
    return {"configured": True, "names": names, "dns_names": dns_names, "ip_addresses": ip_names,
            "not_before": leaf.not_valid_before_utc.isoformat(),
            "not_after": leaf.not_valid_after_utc.isoformat(),
            "valid_until": min(item.not_valid_after_utc for item in chain).isoformat(),
            "fingerprint_sha256": leaf.fingerprint(hashes.SHA256()).hex(),
            "valid": True, "browser_trust_verified": False,
            "validation": "Ключ, SAN, срок и цепочка проверены локально; доверие браузера не проверено"}, normalized


@dataclass(slots=True, repr=False)
class PreparedCertificate:
    certificate_pem: str
    private_key_pem: str
    metadata: dict
    context: ssl.SSLContext


class WebCertificateStore:
    def __init__(self, brain_dir: Path | str, hostname_provider: Callable[[], str]):
        self.root = security_root(brain_dir)
        self.path = self.root / "certificate.secret"
        self.hostname_provider = hostname_provider
        self.lock = threading.RLock()
        self._context: ssl.SSLContext | None = None
        self._metadata: dict = {"configured": False, "valid": False, "browser_trust_verified": False}
        self._load()

    def _context_for(self, certificate_pem: str, key_pem: str) -> ssl.SSLContext:
        """OpenSSL читает ключ из временного файла в закрытом каталоге."""
        paths: list[Path] = []
        try:
            for material in (certificate_pem, key_pem):
                fd, name = tempfile.mkstemp(prefix=".tls-", suffix=".pem", dir=self.root)
                path = Path(name)
                paths.append(path)
                with os.fdopen(fd, "w", encoding="ascii", newline="\n") as stream:
                    stream.write(material)
                private_path(path)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(paths[0]), str(paths[1]))
            return context
        except (OSError, ssl.SSLError, UnicodeError) as exc:
            raise ValueError("OpenSSL не смог подготовить HTTPS-контекст; прежний сертификат сохранён") from exc
        finally:
            for path in paths:
                path.unlink(missing_ok=True)

    def _load(self) -> None:
        reject_link(self.path)
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError("Хранилище сертификата превышает допустимый размер")
            material = json.loads(load_secret(self.path))
            metadata, key = inspect_certificate(material["certificate_pem"], material["private_key_pem"],
                                                 self.hostname_provider())
            context = self._context_for(material["certificate_pem"], key)
            self._metadata, self._context = metadata, context
        except Exception:
            self._metadata = {"configured": True, "valid": False, "browser_trust_verified": False,
                              "error": "Сохранённый сертификат недоступен или не прошёл проверку; импортируйте действующий"}

    def prepare(self, certificate_pem: str, private_key_pem: str,
                password: str | None = None) -> PreparedCertificate:
        with self.lock:
            metadata, normalized_key = inspect_certificate(certificate_pem, private_key_pem,
                                                            self.hostname_provider(), password)
            context = self._context_for(certificate_pem, normalized_key)
            return PreparedCertificate(certificate_pem, normalized_key, metadata, context)

    def install_prepared(self, prepared: PreparedCertificate) -> dict:
        with self.lock:
            if not isinstance(prepared, PreparedCertificate):
                raise ValueError("Сертификат не прошёл предварительную проверку")
            if (datetime.fromisoformat(prepared.metadata["valid_until"]) <= datetime.now(timezone.utc)
                    or not covers_hostname(prepared.metadata, self.hostname_provider())):
                raise ValueError("Срок или имя сертификата изменились до активации; повторите проверку")
            reject_link(self.path)
            reject_link(self.path.with_suffix(self.path.suffix + ".tmp"))
            save_secret(self.path, json.dumps({"certificate_pem": prepared.certificate_pem,
                                               "private_key_pem": prepared.private_key_pem}, ensure_ascii=True))
            self._context, self._metadata = prepared.context, prepared.metadata
            return dict(prepared.metadata)

    def install(self, certificate_pem: str, private_key_pem: str, password: str | None = None) -> dict:
        with self.lock:
            return self.install_prepared(self.prepare(certificate_pem, private_key_pem, password))

    def context(self) -> ssl.SSLContext | None:
        with self.lock:
            if self._context and datetime.fromisoformat(self._metadata["valid_until"]) > datetime.now(timezone.utc):
                return self._context
            return None

    def status(self) -> dict:
        with self.lock:
            value = dict(self._metadata)
            if value.get("valid_until"):
                value["valid"] = datetime.fromisoformat(value["valid_until"]) > datetime.now(timezone.utc)
            return value
