"""Ограниченный ACME v2 / HTTP-01 по RFC 8555, без внешнего клиента и служб.

Согласие владельца относится к точным доменам, контакту, среде CA и редакции
условий. Неизвестный результат запроса запрещает автоматический повтор.
Этот клиент выпускает сертификаты для DNS-имён; профили сертификатов для IP
пока не реализованы, хотя центр сертификации может их поддерживать.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import ssl
import threading
import time
from typing import Any, Callable
from urllib import error, request
from urllib.parse import quote, urlsplit

from .secret_store import load_secret, save_secret
from .web_transport_settings import atomic_json, private_path, reject_link, security_root

DIRECTORIES = {
    "production": "https://acme-v02.api.letsencrypt.org/directory",
    "staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
}
_TOKEN = re.compile(r"[A-Za-z0-9_-]{22,256}\Z")
_NONCE = re.compile(r"[A-Za-z0-9_-]{1,1024}\Z")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_EMAIL = re.compile(r"[^\s@\x00-\x1f\x7f]{1,64}@[a-zA-Z0-9.-]{3,253}\Z")
_MAX_RESPONSE = 256 * 1024
_JOB_SECONDS = 240.0
_DEFAULTS = {"enabled": False, "domains": [], "email": "", "environment": "staging",
             "auto_renew": False, "terms_url": "", "consent_scope": "",
             "state": "disabled", "attention_required": False, "outcome_unknown": False,
             "last_error": "", "expires_at": 0.0, "renew_at": 0.0}


class _AcmeFailure(RuntimeError):
    """Только собственное безопасное сообщение, без тела ответа или секрета."""


class _UnknownOutcome(_AcmeFailure):
    pass


class _Cancelled(_AcmeFailure):
    pass


class _CAProblem(_AcmeFailure):
    def __init__(self, code: str):
        self.code = code
        super().__init__("Центр сертификации отклонил запрос; проверьте домен и условия выпуска")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _object(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise _AcmeFailure("Некорректный ответ центра сертификации") from exc
    if not isinstance(value, dict):
        raise _AcmeFailure("Некорректный ответ центра сертификации")
    return value


def _domains(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise ValueError("Укажите от одного до пяти публичных доменных имён")
    result = []
    for raw in value:
        if not isinstance(raw, str) or not raw or len(raw) > 253:
            raise ValueError("Некорректное доменное имя ACME")
        try:
            domain = raw.strip().rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Некорректное доменное имя ACME") from exc
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError("Эта реализация бесплатного выпуска поддерживает доменные имена; для IP загрузите подходящий сертификат")
        if (len(domain) > 253 or "." not in domain
                or any(not _LABEL.fullmatch(label) for label in domain.split("."))
                or domain.endswith((".localhost", ".local", ".lan", ".internal", ".test",
                                    ".invalid", ".example", ".onion", ".home", ".home.arpa"))):
            raise ValueError("Укажите публичный домен без схемы, порта, пути и wildcard")
        if domain not in result:
            result.append(domain)
    return sorted(result)


def _scope(config: dict) -> str:
    return hashlib.sha256(_json({key: config[key] for key in
                                ("domains", "email", "environment", "terms_url")})).hexdigest()


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else ""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _AcmeWire:
    """JWS RS256, один повтор только при явном badNonce, строгий origin CA."""

    def __init__(self, environment: str, guard: Callable[[], None], deadline: float):
        self.url = DIRECTORIES[environment]
        self.origin = urlsplit(self.url).hostname
        self.guard, self.deadline = guard, deadline
        self.key = None
        self.kid = ""
        self.nonce = ""
        self.directory: dict = {}
        self.opener = request.build_opener(request.ProxyHandler({}),
                                          request.HTTPSHandler(context=ssl.create_default_context()),
                                          _NoRedirect())

    def check_url(self, value: Any) -> str:
        if (not isinstance(value, str) or len(value) > 2048
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise _AcmeFailure("Некорректный адрес ресурса ACME")
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme == "https" and parsed.hostname == self.origin
                     and parsed.port in (None, 443) and parsed.username is None
                     and parsed.password is None and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise _AcmeFailure("Ресурс ACME вышел за разрешённый адрес центра сертификации")
        return value

    def raw(self, method: str, url: str, data: bytes | None = None,
            *, accept: str = "application/json") -> tuple[int, dict, bytes]:
        self.guard()
        self.check_url(url)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _AcmeFailure("Истекло ограниченное время операции ACME")
        headers = {"Accept": accept, "User-Agent": "Leshy-AI/ACME-v2"}
        if data is not None:
            headers["Content-Type"] = "application/jose+json"
        req = request.Request(url, data=data, headers=headers, method=method)
        response = None
        try:
            try:
                response = self.opener.open(req, timeout=min(10.0, remaining))
            except error.HTTPError as exc:
                response = exc
            chunks, total = [], 0
            while method != "HEAD":
                self.guard()
                if time.monotonic() >= self.deadline:
                    raise TimeoutError()
                part = response.read1(min(8192, _MAX_RESPONSE + 1 - total))
                if not part:
                    break
                chunks.append(part)
                total += len(part)
                if total > _MAX_RESPONSE:
                    raise _AcmeFailure("Ответ центра сертификации превышает допустимый размер")
            reply_headers = {key.lower(): value for key, value in response.headers.items()}
            nonce = reply_headers.get("replay-nonce", "")
            if _NONCE.fullmatch(nonce):
                self.nonce = nonce
            return response.getcode(), reply_headers, b"".join(chunks)
        except (error.URLError, OSError, TimeoutError) as exc:
            if method == "POST":
                raise _UnknownOutcome("Ответ ACME потерян; автоматический повтор запрещён") from exc
            raise _AcmeFailure("Не удалось прочитать ресурс центра сертификации") from exc
        finally:
            if response is not None:
                response.close()

    def discover(self) -> str:
        code, _, raw = self.raw("GET", self.url)
        if code != 200:
            raise _AcmeFailure("Каталог центра сертификации недоступен")
        self.directory = _object(raw)
        for name in ("newNonce", "newAccount", "newOrder"):
            self.check_url(self.directory.get(name))
        meta = self.directory.get("meta")
        terms = meta.get("termsOfService", "") if isinstance(meta, dict) else ""
        if not isinstance(terms, str) or len(terms) > 2048:
            raise _AcmeFailure("Центр сертификации не сообщил действующие условия")
        try:
            parsed = urlsplit(terms)
            valid_terms = (parsed.scheme == "https" and parsed.hostname in {"letsencrypt.org", "www.letsencrypt.org"}
                           and not parsed.username and not parsed.password and parsed.port in (None, 443))
        except ValueError:
            valid_terms = False
        if not valid_terms:
            raise _AcmeFailure("Адрес условий центра сертификации не разрешён")
        return terms

    def jwk(self) -> dict:
        numbers = self.key.public_key().public_numbers()
        encode = lambda number: _b64(number.to_bytes((number.bit_length() + 7) // 8, "big"))
        return {"e": encode(numbers.e), "kty": "RSA", "n": encode(numbers.n)}

    def post(self, url: str, payload: dict | None, *, account: bool = True,
             certificate: bool = False) -> tuple[dict | bytes, dict]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        self.check_url(url)
        for attempt in range(2):
            if not self.nonce:
                code, _, _ = self.raw("HEAD", self.directory["newNonce"])
                if code not in (200, 204) or not self.nonce:
                    raise _AcmeFailure("Не получен одноразовый nonce центра сертификации")
            nonce, self.nonce = self.nonce, ""
            protected = {"alg": "RS256", "nonce": nonce, "url": url}
            if account:
                protected["kid"] = self.check_url(self.kid)
            else:
                protected["jwk"] = self.jwk()
            header = _b64(_json(protected))
            body = _b64(_json(payload)) if payload is not None else ""
            signature = self.key.sign((header + "." + body).encode("ascii"),
                                      padding.PKCS1v15(), hashes.SHA256())
            try:
                code, reply_headers, raw = self.raw(
                    "POST", url, _json({"protected": header, "payload": body,
                                        "signature": _b64(signature)}),
                    accept="application/pem-certificate-chain" if certificate else "application/json",
                )
            except (_Cancelled, _UnknownOutcome):
                raise
            except _AcmeFailure as exc:
                if payload is not None:
                    raise _UnknownOutcome("Ответ ACME не удалось проверить; автоматический повтор запрещён") from exc
                raise
            if 200 <= code < 300:
                if certificate:
                    if not reply_headers.get("content-type", "").lower().startswith("application/pem-certificate-chain"):
                        raise _AcmeFailure("Центр сертификации вернул не цепочку PEM")
                    return raw, reply_headers
                try:
                    return _object(raw), reply_headers
                except _AcmeFailure as exc:
                    if payload is not None:
                        raise _UnknownOutcome("Ответ ACME не удалось проверить; автоматический повтор запрещён") from exc
                    raise
            problem = _object(raw)
            problem_type = problem.get("type", "")
            prefix = "urn:ietf:params:acme:error:"
            name = problem_type[len(prefix):] if isinstance(problem_type, str) and problem_type.startswith(prefix) else "rejected"
            # CA явно отклонил неверный nonce: единственный безопасный повтор POST.
            if code == 400 and name == "badNonce" and self.nonce and attempt == 0:
                continue
            raise _CAProblem(name if re.fullmatch(r"[A-Za-z]{1,60}", name) else "rejected")
        raise _AcmeFailure("Центр сертификации повторно отклонил nonce")


class WebAcmeService:
    """Выпуск и ограниченный цикл продления только согласованной области."""

    def __init__(self, brain_dir: Path | str, install_certificate: Callable[[str, str], dict]):
        self.root = security_root(brain_dir) / "acme"
        reject_link(self.root)
        self.root.mkdir(mode=0o700, exist_ok=True)
        private_path(self.root)
        self.path = self.root / "state.json"
        self.install_certificate = install_certificate
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._generation = 0
        self._job: threading.Thread | None = None
        self._renew_thread: threading.Thread | None = None
        self._challenges: dict[str, tuple[int, str]] = {}
        self.config = dict(_DEFAULTS)
        reject_link(self.path)
        if self.path.is_file():
            try:
                if self.path.stat().st_size > 16384:
                    raise ValueError()
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise ValueError()
                self.config.update({key: saved[key] for key in _DEFAULTS if key in saved})
                for key in ("enabled", "auto_renew", "attention_required", "outcome_unknown"):
                    if type(self.config[key]) is not bool:
                        raise ValueError()
                if self.config["environment"] not in DIRECTORIES or not isinstance(self.config["domains"], list):
                    raise ValueError()
                if self.config["domains"]:
                    self.config["domains"] = _domains(self.config["domains"])
                for key in ("email", "terms_url", "consent_scope", "state", "last_error"):
                    if not isinstance(self.config[key], str) or len(self.config[key]) > 2048:
                        raise ValueError()
                for key in ("expires_at", "renew_at"):
                    number = self.config[key]
                    if type(number) not in (float, int) or not math.isfinite(number) or not 0 <= number < 253402300799:
                        raise ValueError()
                if self.config["enabled"]:
                    self._validate(self.config)
                    if self.config["consent_scope"] != _scope(self.config):
                        raise ValueError()
                if self.config["state"] == "issuing":
                    self.config.update(state="outcome_unknown", attention_required=True,
                                       outcome_unknown=True, last_error="Предыдущий выпуск был прерван; требуется проверка владельцем")
            except (ValueError, TypeError, OSError, KeyError):
                self.config = {**_DEFAULTS, "state": "error", "last_error": "Настройки ACME повреждены; выпуск отключён"}

    @staticmethod
    def _validate(value: dict) -> dict:
        if value.get("environment") not in DIRECTORIES:
            raise ValueError("Выберите production или staging центра Let's Encrypt")
        for name in ("enabled", "auto_renew"):
            if type(value.get(name)) is not bool:
                raise ValueError("Переключатели ACME должны быть логическими")
        value["domains"] = _domains(value.get("domains"))
        email = value.get("email")
        if not isinstance(email, str) or not _EMAIL.fullmatch(email) or len(email) > 254:
            raise ValueError("Введите корректный контактный email для центра сертификации")
        return value

    def _save(self) -> None:
        atomic_json(self.path, self.config)

    def status(self) -> dict:
        with self._lock:
            value = self.config
            consent = bool(value["consent_scope"] and value["consent_scope"] == _scope(value))
            return {"enabled": value["enabled"], "domains": list(value["domains"]),
                    "hostname": value["domains"][0] if value["domains"] else "",
                    "email": value["email"], "environment": value["environment"],
                    "auto_renew": value["auto_renew"], "terms_url": value["terms_url"],
                    "terms_agreed": consent, "contact_consent": consent,
                    "state": value["state"], "busy": bool(self._job and self._job.is_alive()),
                    "in_progress": bool(self._job and self._job.is_alive()),
                    "attention_required": value["attention_required"],
                    "outcome_unknown": value["outcome_unknown"], "last_error": value["last_error"],
                    "expires_at": _iso(value["expires_at"]), "renew_at": _iso(value["renew_at"]),
                    "challenge_type": "http-01", "public_http_port": 80,
                    "notice": "HTTP-01 требует доступного из Интернета порта 80. Staging-сертификат не доверен браузерами."}

    def configure(self, body: dict) -> dict:
        if not isinstance(body, dict) or body.get("confirm") is not True:
            raise ValueError("Подтвердите изменение ACME и области согласия")
        allowed = {"confirm", "enabled", "domains", "hostname", "email", "environment",
                   "auto_renew", "terms_url", "terms_agreed", "contact_consent"}
        if set(body) - allowed:
            raise ValueError("Неизвестные параметры ACME")
        with self._lock:
            previous_scope = self.config["consent_scope"]
            previous_expiry, previous_renewal = self.config["expires_at"], self.config["renew_at"]
            previous_attention, previous_unknown = self.config["attention_required"], self.config["outcome_unknown"]
            previous_error = self.config["last_error"]
            interrupted = bool(self._job and self._job.is_alive())
            if interrupted:
                previous_attention = previous_unknown = True
                previous_error = "Настройки изменены во время выпуска; проверьте его результат до нового запроса"
            candidate = {**self.config, **{key: body[key] for key in
                                         ("enabled", "domains", "email", "environment", "auto_renew") if key in body}}
            if "hostname" in body and "domains" not in body:
                candidate["domains"] = [body["hostname"]]
            if body.get("enabled") is False and set(body) <= {"enabled", "confirm"}:
                self._generation += 1
                self._challenges.clear()
                self.config.update(enabled=False, auto_renew=False, consent_scope="", state="disabled")
                self.config.update(attention_required=previous_attention, outcome_unknown=previous_unknown,
                                   last_error=previous_error if previous_attention else "")
                self._save()
                return self.status()
            candidate["enabled"] = body.get("enabled", True)
            self._validate(candidate)
            # Изменение отзывается до сетевого чтения условий, а не после него.
            self._generation += 1
            generation = self._generation
            self._challenges.clear()
            requested_enable = candidate["enabled"]
            # Отзыв согласия или новый scope не отменяет уже начатое внешнее действие.
            candidate.update(enabled=False, consent_scope="", state="needs_consent",
                             attention_required=previous_attention, outcome_unknown=previous_unknown,
                             last_error=previous_error if previous_attention else "")
            self.config = candidate
            self._save()
        wire = _AcmeWire(candidate["environment"], lambda: self._guard_generation(generation), time.monotonic() + 20)
        try:
            terms = wire.discover()
        except _AcmeFailure:
            with self._lock:
                if generation == self._generation:
                    self.config.update(state="error", last_error="Не удалось получить действующие условия центра сертификации")
                    self._save()
            return self.status()
        with self._lock:
            self._guard_generation(generation)
            self.config["terms_url"] = terms
            if (requested_enable and body.get("terms_agreed") is True
                    and body.get("contact_consent") is True and body.get("terms_url") == terms):
                scope = _scope(self.config)
                same_scope = bool(previous_scope and previous_scope == scope)
                self.config.update(enabled=True, consent_scope=scope,
                                   state="outcome_unknown" if previous_unknown else "error" if previous_attention else "ready",
                                   attention_required=previous_attention,
                                   outcome_unknown=previous_unknown,
                                   last_error=previous_error if previous_attention else "",
                                   expires_at=previous_expiry if same_scope else 0.0,
                                   renew_at=previous_renewal if same_scope else 0.0)
            self._save()
            return self.status()

    def _guard_generation(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation or self._stop.is_set():
                raise _Cancelled("Настройки ACME изменены либо служба остановлена")

    def replace_certificate_manually(self, install: Callable[[], dict]) -> dict:
        """Отозвать старый job и откатить настройки, если запись сертификата не удалась."""
        with self._lock:
            previous = dict(self.config)
            if self._job and self._job.is_alive():
                previous.update(state="outcome_unknown", attention_required=True, outcome_unknown=True,
                                last_error="Выпуск прерван импортом; проверьте результат до нового запроса")
            self._generation += 1
            self._challenges.clear()
            self.config = {**previous, "enabled": False, "auto_renew": False,
                           "consent_scope": "", "state": "disabled"}
            try:
                self._save()
                # ACME → core: поздняя установка старого заказа исключена тем же lock.
                return install()
            except Exception:
                self.config = previous
                try:
                    self._save()
                except OSError as exc:
                    self.config.update(enabled=False, auto_renew=False, attention_required=True,
                                       last_error="Импорт не завершён; не удалось восстановить настройки продления")
                    raise _AcmeFailure("Импорт не завершён; проверьте сертификат и настройки продления") from exc
                raise

    def _guard_job(self, generation: int, scope: str) -> None:
        self._guard_generation(generation)
        with self._lock:
            if not self.config["enabled"] or self.config["consent_scope"] != scope or _scope(self.config) != scope:
                raise _Cancelled("Согласие на выпуск отозвано")

    def issue(self, body: dict) -> dict:
        if not isinstance(body, dict) or body.get("confirm") is not True or set(body) - {"confirm", "abandon_unknown"}:
            raise ValueError("Подтвердите выпуск сертификата для сохранённой области ACME")
        with self._lock:
            if self.config["outcome_unknown"] and body.get("abandon_unknown") is not True:
                raise ValueError("Проверьте прошлый результат; новый выпуск требует отдельного abandon_unknown=true")
            self._launch()
            return self.status()

    def _launch(self) -> None:
        if self._job and self._job.is_alive():
            raise ValueError("Выпуск ACME уже выполняется")
        generation, scope = self._generation, self.config["consent_scope"]
        self._guard_job(generation, scope)
        snapshot = dict(self.config)
        self.config.update(state="issuing", last_error="", attention_required=False, outcome_unknown=False)
        try:
            self._save()
            self._job = threading.Thread(target=self._run, args=(generation, scope, snapshot),
                                         name="leshy-acme-issue", daemon=True)
            self._job.start()
        except (OSError, RuntimeError) as exc:
            self._job = None
            self.config.update(state="error", attention_required=True,
                               last_error="Не удалось сохранить или запустить выпуск ACME")
            try:
                self._save()
            except OSError:
                pass
            raise _AcmeFailure("Не удалось сохранить или запустить выпуск ACME") from exc

    def challenge(self, token: str) -> str | None:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            return None
        with self._lock:
            item = self._challenges.get(token)
            if item and item[0] == self._generation and self.config["enabled"] and not self._stop.is_set():
                return item[1]
            return None

    def _pause(self, headers: dict, guard: Callable[[], None], deadline: float) -> None:
        delay = 2.0
        value = headers.get("retry-after", "")
        try:
            delay = float(value) if value.isdigit() else parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            pass
        # Retry-After не сокращается: при слишком большой задержке закончится job,
        # а не начнётся преждевременный повтор. Отмена проверяется каждую секунду.
        wake_at = time.monotonic() + max(1.0, delay)
        while True:
            guard()
            now = time.monotonic()
            if now >= deadline:
                raise _AcmeFailure("Истекло ограниченное время операции ACME")
            if now >= wake_at:
                return
            self._stop.wait(min(1.0, wake_at - now, deadline - now))

    def _poll(self, wire: _AcmeWire, url: str, done: set[str], guard: Callable[[], None]) -> dict:
        for _ in range(30):
            guard()
            value, headers = wire.post(url, None)
            state = value.get("status")
            if state in done:
                return value
            if state not in {"pending", "processing", "ready"}:
                raise _AcmeFailure("Центр сертификации не подтвердил домен или заказ")
            self._pause(headers, guard, wire.deadline)
        raise _AcmeFailure("Превышено число проверок состояния ACME")

    def _account(self, wire: _AcmeWire, config: dict, guard: Callable[[], None]) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        suffix = hashlib.sha256(config["email"].encode("utf-8")).hexdigest()[:16]
        path = self.root / ("account-" + config["environment"] + "-" + suffix + ".secret")
        reject_link(path)
        reject_link(path.with_suffix(path.suffix + ".tmp"))
        guard()
        if path.is_file():
            if path.stat().st_size > 16384:
                raise _AcmeFailure("Закрытый файл учётной записи ACME повреждён")
            private_path(path)
            if os.name == "nt" and not path.read_bytes().startswith(b"dpapi:"):
                raise _AcmeFailure("Учётная запись ACME должна храниться с защитой Windows DPAPI")
            saved = _object(load_secret(path).encode("utf-8"))
            wire.key = serialization.load_pem_private_key(saved["key"].encode("ascii"), password=None)
            if not isinstance(wire.key, rsa.RSAPrivateKey) or wire.key.key_size < 2048:
                raise _AcmeFailure("Некорректный закрытый ключ учётной записи ACME")
            if saved.get("account_url"):
                wire.kid = wire.check_url(saved["account_url"])
                return
        else:
            wire.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = wire.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode("ascii")
            saved = {"key": pem, "account_url": ""}
            guard()
            save_secret(path, json.dumps(saved))
            private_path(path)
        # Поиск по тому же ключу не создаёт второй аккаунт после потери ответа.
        try:
            value, headers = wire.post(wire.directory["newAccount"], {"onlyReturnExisting": True}, account=False)
        except _CAProblem as exc:
            if exc.code != "accountDoesNotExist":
                raise
            value, headers = wire.post(wire.directory["newAccount"],
                                       {"termsOfServiceAgreed": True, "contact": ["mailto:" + quote(config["email"], safe="@.+-")]},
                                       account=False)
        if value.get("status") != "valid":
            raise _AcmeFailure("Учётная запись ACME недоступна")
        wire.kid = wire.check_url(headers.get("location"))
        guard()
        saved["account_url"] = wire.kid
        save_secret(path, json.dumps(saved))
        private_path(path)

    def _run(self, generation: int, scope: str, config: dict) -> None:
        guard = lambda: self._guard_job(generation, scope)
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            wire = _AcmeWire(config["environment"], guard, time.monotonic() + _JOB_SECONDS)
            terms = wire.discover()
            if terms != config["terms_url"]:
                with self._lock:
                    guard()
                    self._generation += 1
                    self.config.update(enabled=False, auto_renew=False, consent_scope="", terms_url=terms,
                                       state="needs_consent", last_error="Условия CA изменились; требуется новое согласие")
                    self._save()
                return
            self._account(wire, config, guard)
            guard()
            certificate_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([]))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain) for domain in config["domains"]]), critical=False)
                   .sign(certificate_key, hashes.SHA256()))
            order, headers = wire.post(wire.directory["newOrder"],
                                        {"identifiers": [{"type": "dns", "value": domain} for domain in config["domains"]]})
            order_url = wire.check_url(headers.get("location"))
            requested = {(item.get("type"), item.get("value")) for item in order.get("identifiers", []) if isinstance(item, dict)}
            if requested != {("dns", domain) for domain in config["domains"]}:
                raise _AcmeFailure("Заказ ACME содержит другую область доменов")
            authorizations = order.get("authorizations")
            if not isinstance(authorizations, list) or not 1 <= len(authorizations) <= len(config["domains"]):
                raise _AcmeFailure("Некорректный список проверок домена ACME")
            thumbprint = _b64(hashlib.sha256(_json(wire.jwk())).digest())
            for address in authorizations:
                authorization, _ = wire.post(wire.check_url(address), None)
                identifier = authorization.get("identifier", {})
                if (not isinstance(identifier, dict) or identifier.get("type") != "dns"
                        or identifier.get("value") not in config["domains"] or authorization.get("wildcard")):
                    raise _AcmeFailure("Проверка ACME не относится к согласованному домену")
                if authorization.get("status") == "valid":
                    continue
                choices = authorization.get("challenges", [])
                challenge = next((item for item in choices if isinstance(item, dict) and item.get("type") == "http-01"), None)
                token = challenge.get("token", "") if challenge else ""
                if not isinstance(token, str) or not _TOKEN.fullmatch(token):
                    raise _AcmeFailure("CA не предложил допустимую проверку HTTP-01")
                with self._lock:
                    guard()
                    self._challenges[token] = (generation, token + "." + thumbprint)
                wire.post(wire.check_url(challenge.get("url")), {})
                self._poll(wire, address, {"valid"}, guard)
            ready = self._poll(wire, order_url, {"ready", "valid"}, guard)
            if ready.get("status") != "valid":
                wire.post(wire.check_url(order.get("finalize")),
                          {"csr": _b64(csr.public_bytes(serialization.Encoding.DER))})
                ready = self._poll(wire, order_url, {"valid"}, guard)
            chain, _ = wire.post(wire.check_url(ready.get("certificate")), None, certificate=True)
            certificates = x509.load_pem_x509_certificates(chain)
            if not certificates or len(certificates) > 10:
                raise _AcmeFailure("Некорректная цепочка сертификатов ACME")
            leaf = certificates[0]
            names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            if (any(not isinstance(name, x509.DNSName) for name in names)
                    or set(names.get_values_for_type(x509.DNSName)) != set(config["domains"])):
                raise _AcmeFailure("Сертификат ACME не соответствует согласованным доменам")
            public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            if leaf.public_key().public_bytes(*public_format) != certificate_key.public_key().public_bytes(*public_format):
                raise _AcmeFailure("Сертификат ACME не соответствует созданному ключу")
            private_pem = certificate_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()).decode("ascii")
            not_before, not_after = leaf.not_valid_before_utc.timestamp(), leaf.not_valid_after_utc.timestamp()
            if not_after <= time.time() or not_before > time.time() + 300 or not_after <= not_before:
                raise _AcmeFailure("Срок действия выданного сертификата не подходит для текущего подключения")
            with self._lock:
                # Единый порядок блокировок: ACME → TLS; после отзыва callback не вызывается.
                guard()
                self.install_certificate(chain.decode("ascii"), private_pem)
                renewal = not_after - min(30 * 86400.0, (not_after - not_before) / 3)
                self.config.update(state="issued", expires_at=not_after, renew_at=renewal,
                                   attention_required=False, outcome_unknown=False, last_error="")
                if renewal <= time.time() + 300:
                    self.config.update(auto_renew=False, attention_required=True,
                                       last_error="Срок сертификата слишком короток для автопродления; проверьте выпуск")
                self._save()
        except _Cancelled:
            pass
        except Exception as exc:
            with self._lock:
                if generation == self._generation:
                    unknown = isinstance(exc, _UnknownOutcome)
                    message = str(exc) if isinstance(exc, _AcmeFailure) else "Выпуск ACME не завершён; требуется проверка владельцем"
                    self.config.update(state="outcome_unknown" if unknown else "error", attention_required=True,
                                       outcome_unknown=unknown, last_error=message)
                    try:
                        self._save()
                    except OSError:
                        self.config["last_error"] = "Не удалось сохранить состояние ACME; автоматическое продление остановлено"
        finally:
            with self._lock:
                self._challenges = {token: item for token, item in self._challenges.items() if item[0] != generation}

    def start(self) -> None:
        with self._lock:
            if self._renew_thread and self._renew_thread.is_alive():
                return
            self._stop.clear()
            self._renew_thread = threading.Thread(target=self._renew_loop, name="leshy-acme-renew", daemon=True)
            self._renew_thread.start()

    def _renew_loop(self) -> None:
        while not self._stop.wait(60.0):
            with self._lock:
                value = self.config
                if (value["enabled"] and value["auto_renew"] and not value["attention_required"]
                        and value["renew_at"] and time.time() >= value["renew_at"]
                        and not (self._job and self._job.is_alive())):
                    try:
                        self._launch()
                    except (ValueError, _AcmeFailure, OSError):
                        value.update(state="error", attention_required=True,
                                     last_error="Автопродление остановлено; проверьте настройки ACME")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._generation += 1
            self._challenges.clear()
            if self.config["state"] == "issuing":
                self.config.update(state="outcome_unknown", attention_required=True, outcome_unknown=True,
                                   last_error="Выпуск остановлен; проверьте прошлый результат до нового запроса")
                self._save()


__all__ = ["WebAcmeService"]
