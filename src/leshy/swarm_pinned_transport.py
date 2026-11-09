from __future__ import annotations

"""Pinned HTTPS for untrusted public swarm seed hosts.

No proxy is used for public seeds: proxy-side DNS cannot be independently
validated by this client. Fail closed instead of silently bypassing policy.
"""

import http.client
import ipaddress
import socket
import ssl
from urllib import parse, request


def _public_addresses(host: str, port: int) -> tuple[str, ...]:
    addresses = []
    for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        ip = entry[4][0].split("%", 1)[0]
        if not ipaddress.ip_address(ip).is_global:
            raise ValueError("Seed DNS содержит непубличный IP")
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise ValueError("Seed DNS не вернул адресов")
    return tuple(addresses)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self) -> None:
        # DNS is NOT performed a second time. TLS still verifies original host.
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _effective_https_proxy(host: str) -> bool:
    """Only refuse a proxy actually applicable to this HTTPS destination.

    NO_PROXY/HTTP_PROXY alone do not proxy HTTPS. Honour platform-specific
    bypass rules (including Windows proxy settings) before deciding.
    """
    proxies = request.getproxies()
    if not (proxies.get("https") or proxies.get("all")):
        return False
    return not request.proxy_bypass(host)


def post_seed_hello(url: str, payload: bytes, *, timeout: float = 5.0,
                    max_bytes: int = 4096, allow_local_http: bool = False) -> bytes:
    parsed = parse.urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    local = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "http" and allow_local_http and local:
        # The local pilot is not a general-purpose HTTP client: reject
        # credentials, alternate paths and query/fragment before any I/O.
        if (parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or parsed.path != "/v1/swarm/hello"):
            raise ValueError("Недопустимый маршрут локального seed")
        opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        req = request.Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
        })
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError("Bootstrap отклонил регистрацию")
            return response.read(max_bytes + 1)
    if parsed.scheme != "https" or local or parsed.username or parsed.password:
        raise ValueError("Публичный seed требует HTTPS и публичный адрес")
    if parsed.query or parsed.fragment or parsed.path != "/v1/swarm/hello":
        raise ValueError("Недопустимый маршрут публичного seed")
    # A configured proxy may resolve the hostname itself, bypassing DNS pinning.
    # Refuse until a verified proxy transport is implemented.
    if _effective_https_proxy(host):
        raise ValueError("Публичный swarm seed через прокси пока запрещён")
    ips = _public_addresses(host, port)
    conn = _PinnedHTTPS(host, port, ips[0], timeout)
    try:
        conn.request("POST", parsed.path, body=payload, headers={
            "Content-Type": "application/json", "Accept": "application/json",
        })
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError("Bootstrap отклонил регистрацию")
        return response.read(max_bytes + 1)
    finally:
        conn.close()


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
