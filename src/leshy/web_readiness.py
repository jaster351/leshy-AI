"""Ограниченная проверка ответа собственного HTTP(S) handler перед browser callback."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import time
from typing import Callable

from .web_acme import _DeadlineHTTPResponse


_HEX = re.compile(r"[0-9a-f]{64}\Z")
READINESS_PATH = "/v1/web-ready"


def response_proof(secret: bytes, nonce: str) -> str:
    if not isinstance(secret, bytes) or len(secret) != 32 or not isinstance(nonce, str) or not _HEX.fullmatch(nonce):
        raise ValueError("Некорректный запрос проверки готовности")
    return hmac.new(secret, nonce.encode("ascii"), hashlib.sha256).hexdigest()


def _target(family: int, address: tuple) -> tuple:
    if not isinstance(address, tuple) or len(address) not in {2, 4}:
        raise ValueError("Нет числового адреса listener")
    host, port = address[:2]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Нет фактического порта listener")
    value = ipaddress.ip_address(host)
    if value.is_multicast or (family == socket.AF_INET) != (value.version == 4):
        raise ValueError("Неподходящее семейство listener")
    if family == socket.AF_INET:
        return ("127.0.0.1" if value.is_unspecified else str(value), port)
    if family != socket.AF_INET6:
        raise ValueError("Неподдерживаемое семейство listener")
    scope = address[3] if len(address) == 4 else 0
    if type(scope) is not int or scope < 0 or ("%" in str(value) and not scope):
        raise ValueError("Нет числовой зоны IPv6 listener")
    return ("::1" if value.is_unspecified else str(value).split("%", 1)[0], port, 0, scope)


def probe_listener(*, family: int, address: tuple, secret: bytes,
                   tls_context: ssl.SSLContext | None, server_hostname: str,
                   fingerprint_sha256: str, deadline: float,
                   stopped: Callable[[], bool]) -> bool:
    """Без DNS/proxy/credentials: только numeric binding и MAC своего handler.

    TLS проверяет SAN, срок и локально доверенную публичную цепочку, затем
    точный leaf fingerprint до отправки GET. Это не проверка доверия браузера.
    """
    raw = connection = response = None

    def guard():
        if stopped() or time.monotonic() >= deadline:
            raise TimeoutError("Проверка готовности остановлена или истекла")

    def remaining():
        guard()
        return max(0.001, deadline - time.monotonic())

    try:
        target = _target(family, address)
        nonce = secrets.token_hex(32)
        expected = response_proof(secret, nonce)
        if tls_context is not None:
            if (tls_context.verify_mode != ssl.CERT_REQUIRED or tls_context.check_hostname is not True
                    or not tls_context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
                    or not server_hostname or not _HEX.fullmatch(fingerprint_sha256)):
                return False
        elif server_hostname or fingerprint_sha256:
            return False
        guard()
        raw = socket.socket(family, socket.SOCK_STREAM)
        raw.settimeout(remaining())
        raw.connect(target)
        if tls_context is not None:
            raw.settimeout(remaining())
            raw = tls_context.wrap_socket(raw, server_hostname=server_hostname)
            guard()
            if not hmac.compare_digest(hashlib.sha256(raw.getpeercert(binary_form=True)).hexdigest(), fingerprint_sha256):
                return False
        raw.settimeout(remaining())
        host = server_hostname or target[0]
        authority = "[" + host + "]" if ":" in host else host
        authority += ":" + str(target[1])
        connection = http.client.HTTPConnection(host, target[1], timeout=remaining())
        connection.sock = raw  # Уже числовой connect; HTTPConnection.connect/DNS не вызывается.
        connection.response_class = lambda *args, **kwargs: _DeadlineHTTPResponse(
            *args, deadline=deadline, guard=guard, **kwargs)
        connection.request("GET", READINESS_PATH, headers={
            "Host": authority, "X-Leshy-Readiness": nonce, "Connection": "close"})
        response = connection.getresponse()
        guard()
        lengths = response.headers.get_all("Content-Length", [])
        if (response.status != 200 or len(lengths) != 1 or not re.fullmatch(r"[1-9][0-9]{0,2}", lengths[0])
                or not 1 <= int(lengths[0]) <= 256 or response.headers.get("Transfer-Encoding")
                or response.headers.get("Content-Encoding")
                or response.headers.get_content_type() != "application/json"):
            return False
        body = bytearray()
        while True:
            guard()
            part = response.read1(min(128, 257 - len(body)))
            if not part:
                break
            body.extend(part)
            if len(body) > 256:
                return False
        guard()
        if len(body) != int(lengths[0]):
            return False
        value = json.loads(body)
        return (isinstance(value, dict) and set(value) == {"proof"}
                and isinstance(value["proof"], str) and hmac.compare_digest(value["proof"], expected))
    except (OSError, ValueError, TypeError, RuntimeError, http.client.HTTPException):
        return False
    finally:
        for resource in (response, connection, raw):
            if resource is not None:
                try:
                    resource.close()
                except (OSError, http.client.HTTPException):
                    pass


__all__ = ["probe_listener", "response_proof", "READINESS_PATH"]
