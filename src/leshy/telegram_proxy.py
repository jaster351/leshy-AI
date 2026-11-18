"""Isolated HTTP CONNECT and SOCKS5 transport for Telegram Bot API.

Credentials are stored via Leshy's secret store (Windows DPAPI).
No global proxy/socket changes: local LLM and browser connections are untouched.
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import threading
from urllib import error, parse, request

from .secret_store import load_secret, save_secret

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+$")


def _telegram_tls_context() -> ssl.SSLContext:
    """Дополнительный доверенный CA для Telegram. Проверка TLS обязательна."""
    ctx = ssl.create_default_context()
    path = os.getenv("LESHY_TELEGRAM_CA_BUNDLE", "").strip()
    if path:
        source = Path(path).expanduser()
        if not source.is_file():
            raise FileNotFoundError("Не найден PEM-файл доверенного CA Telegram")
        ctx.load_verify_locations(cafile=str(source))
    return ctx


def _valid_host(raw: object) -> str:
    host = str(raw or "").strip()
    if not host or len(host) > 253 or any(c in host for c in "@/\\?# \t\r\n"):
        raise ValueError("Некорректный адрес прокси")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host.strip("[]")
    except ValueError:
        if not _HOST_RE.fullmatch(host) or host.startswith((".", "-")) or host.endswith((".", "-")):
            raise ValueError("Некорректное имя прокси")
        return host.lower()


def _hostport(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _normalize(raw: dict) -> dict:
    enabled = bool(raw.get("enabled", False))
    scheme = str(raw.get("scheme") or "http").lower()
    if scheme not in {"http", "socks5"}:
        raise ValueError("Поддерживаются только HTTP и SOCKS5 прокси")
    host = _valid_host(raw.get("host")) if enabled or str(raw.get("host") or "").strip() else ""
    try:
        port = int(raw.get("port") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Порт должен быть числом") from exc
    if (enabled or host) and not (1 <= port <= 65535):
        raise ValueError("Порт прокси должен быть от 1 до 65535")
    auth = bool(raw.get("auth", False))
    username = str(raw.get("username") or "") if auth else ""
    password = str(raw.get("password") or "") if auth else ""
    if len(username) > 256 or len(password) > 1024 or any(c in username + password for c in "\r\n"):
        raise ValueError("Некорректные учётные данные прокси")
    if enabled and auth and (not username or not password):
        raise ValueError("Для авторизации необходимы логин и пароль")
    return {"enabled": enabled, "scheme": scheme, "host": host, "port": port,
            "auth": auth, "username": username, "password": password}


def _public(cfg: dict) -> dict:
    return {"enabled": cfg["enabled"], "scheme": cfg["scheme"],
            "host": cfg["host"], "port": cfg["port"], "auth": cfg["auth"],
            "username": cfg["username"], "has_password": bool(cfg["password"]),
            "custom_ca_configured": bool(os.getenv("LESHY_TELEGRAM_CA_BUNDLE", "").strip())}


class _NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectionError("Telegram перенаправил запрос; переход запрещён")


class _ForceHTTPProxy(request.ProxyHandler):
    """Honor explicitly configured proxy even when NO_PROXY includes Telegram."""
    def proxy_open(self, req, proxy, type):
        if type != "https":
            return None
        value = parse.urlsplit(proxy)
        if value.scheme != "http" or not value.hostname or not value.port:
            raise ValueError("Некорректный адрес HTTP-прокси")
        if value.username and value.password:
            pair = parse.unquote(value.username) + ":" + parse.unquote(value.password)
            auth = base64.b64encode(pair.encode("utf-8")).decode("ascii")
            req.add_header("Proxy-authorization", "Basic " + auth)
        req.set_proxy(_hostport(value.hostname, value.port), "http")
        return None


class _SocksHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *, cfg: dict, **kwargs):
        self._cfg = cfg
        super().__init__(host, **kwargs)

    def connect(self):
        try:
            import socks
        except ImportError as exc:
            raise RuntimeError("Для SOCKS5 установите PySocks") from exc
        cfg = self._cfg
        sock = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, cfg["host"], cfg["port"], rdns=True,
                       username=cfg["username"] if cfg["auth"] else None,
                       password=cfg["password"] if cfg["auth"] else None)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.host, self.port))
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _SocksHTTPSHandler(request.HTTPSHandler):
    def __init__(self, cfg: dict, *, context: ssl.SSLContext | None = None):
        super().__init__(context=context or _telegram_tls_context())
        self._cfg = cfg

    def https_open(self, req):
        return self.do_open(
            lambda host, **kw: _SocksHTTPSConnection(host, cfg=self._cfg, **kw),
            req, context=self._context,
        )


class TelegramProxyTransport:
    """All Telegram network requests share the same saved proxy configuration."""

    def __init__(self, brain_dir: Path):
        self.path = Path(brain_dir) / "secrets" / "telegram_proxy.dat"
        self._lock = threading.RLock()

    def _read(self) -> dict:
        raw = load_secret(self.path)
        if not raw:
            return _normalize({})
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("expected object")
            return _normalize(data)
        except (ValueError, TypeError):
            raise ValueError("Повреждена конфигурация Telegram-прокси") from None

    def status(self) -> dict:
        with self._lock:
            try:
                return _public(self._read())
            except (ValueError, OSError, RuntimeError):
                # Do not crash /v1/status when an old DPAPI secret is invalid.
                return {**_public(_normalize({})), "error": "Настройки прокси повреждены"}

    def configure(self, values: dict) -> dict:
        if not isinstance(values, dict):
            raise ValueError("Ожидается JSON с настройками прокси")
        with self._lock:
            try:
                previous = self._read()
            except (ValueError, OSError, RuntimeError):
                # Owner can replace damaged settings; never silently
                # bypass an active configured proxy during network requests.
                previous = _normalize({})
            data = dict(values)
            if data.get("auth") and not data.get("password") and not data.get("clear_password"):
                # Empty password means "keep", but only for the identical host
                # and username; never reuse a secret at another destination.
                try:
                    same = (
                        previous["auth"]
                        and str(data.get("username") or "") == previous["username"]
                        and str(data.get("scheme") or "http").lower() == previous["scheme"]
                        and _valid_host(data.get("host")) == previous["host"]
                        and int(data.get("port") or 0) == previous["port"]
                    )
                except (ValueError, TypeError):
                    same = False
                if same:
                    data["password"] = previous["password"]
            data.pop("clear_password", None)
            cfg = _normalize(data)
            save_secret(self.path, json.dumps(cfg, ensure_ascii=False, separators=(",", ":")))
            return _public(cfg)

    @staticmethod
    def _opener(cfg: dict):
        if cfg["scheme"] == "http":
            host = _hostport(cfg["host"], cfg["port"])
            if cfg["auth"]:
                username = parse.quote(cfg["username"], safe="")
                password = parse.quote(cfg["password"], safe="")
                address = f"http://{username}:{password}@{host}"
            else:
                address = f"http://{host}"
            return request.build_opener(
                _ForceHTTPProxy({"https": address}),
                request.HTTPSHandler(context=_telegram_tls_context()),
                _NoRedirects(),
            )
        return request.build_opener(
            request.ProxyHandler({}), _SocksHTTPSHandler(cfg, context=_telegram_tls_context()), _NoRedirects(),
        )

    def open(self, req, *, timeout: float):
        url = req.full_url if isinstance(req, request.Request) else str(req)
        target = parse.urlsplit(url)
        # Never allow arbitrary endpoints, redirects or URL credentials to
        # receive the Telegram bot token, regardless of proxy configuration.
        if (target.scheme != "https" or target.hostname != "api.telegram.org"
                or target.port is not None or target.username is not None
                or target.password is not None or target.fragment):
            raise ValueError("Telegram transport accepts only HTTPS Telegram Bot API")

        with self._lock:
            cfg = self._read()
        try:
            if cfg["enabled"]:
                opener = self._opener(cfg)
            else:
                # An explicit opener retains environment/system proxy settings
                # while disabling urllib's automatic HTTP redirects.
                opener = request.build_opener(
                    request.HTTPSHandler(context=_telegram_tls_context()),
                    _NoRedirects(),
                )
            return opener.open(req, timeout=timeout)
        except error.HTTPError as exc:
            # HTTPError.geturl() and str(exc) otherwise expose the bot token.
            # Preserve status, headers (notably Retry-After), and response body.
            raise error.HTTPError(
                "https://api.telegram.org/",
                exc.code,
                exc.msg,
                exc.hdrs,
                exc.fp,
            ) from None
        except Exception as exc:
            reason = exc.reason if isinstance(exc, error.URLError) else exc
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise ConnectionError(
                    "Telegram TLS: сертификат не доверен (CERTIFICATE_VERIFY_FAILED); "
                    "проверьте корневой CA прокси/антивируса"
                ) from None
            # Never expose the request URL or proxy credentials in diagnostics.
            raise ConnectionError(
                f"Telegram transport unavailable ({type(exc).__name__})"
            ) from None


__all__ = ["TelegramProxyTransport"]
