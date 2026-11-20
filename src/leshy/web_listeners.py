"""Общие серверы HTTP/HTTPS для явных привязок IPv4/IPv6."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import ipaddress
import os
import socket
import ssl
import threading


def binding_addresses(host: str, port: int) -> list[tuple[int, tuple]]:
    """Составить привязки без расширения явно выбранного адреса до всех интерфейсов."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("Укажите auto, локальный IP или имя для привязки сервера")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Некорректный порт привязки сервера")
    target = host.strip()
    if target.lower() == "auto":
        return [(socket.AF_INET, ("0.0.0.0", port)),
                (socket.AF_INET6, ("::", port, 0, 0))]
    addresses = []
    seen = set()
    for family, _kind, _protocol, _name, address in socket.getaddrinfo(
            target, port, socket.AF_UNSPEC, socket.SOCK_STREAM):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        address = tuple(address)
        if family == socket.AF_INET6:
            value = ipaddress.ip_address(address[0])
            if value.ipv4_mapped is not None:
                # IPv4-mapped адрес нельзя привязать через IPv6-only сокет.
                family, address = socket.AF_INET, (str(value.ipv4_mapped), address[1])
        key = (family, address)
        if key not in seen:
            seen.add(key)
            addresses.append(key)
    if not addresses:
        raise OSError("Для выбранного адреса нет IPv4/IPv6 привязок")
    return addresses


def _peer_address(address: tuple) -> tuple:
    """Сохранять реальный peer, представляя IPv4-mapped IPv6 обычным IPv4."""
    try:
        value = ipaddress.ip_address(address[0])
    except (ValueError, TypeError, IndexError):
        return address
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped is not None:
        return (str(value.ipv4_mapped), *address[1:])
    return address


class _WebServer(ThreadingHTTPServer):
    daemon_threads = True
    max_connection_workers = 32

    def __init__(self, *args, **kwargs):
        self._worker_slots = threading.BoundedSemaphore(self.max_connection_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # Лимит относится к одному listener и не блокирует accept-loop.
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        # Слот удерживается и при TLS handshake, и при чтении/ответе handler.
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()

    def server_bind(self):
        if os.name == "nt":
            # Windows SO_REUSEADDR допускает захват уже занятого порта.
            # Исключительное владение задаётся до bind и не требует UAC.
            self.allow_reuse_address = False
            self.allow_reuse_port = False
            option = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if option is None:
                raise OSError("Windows не поддерживает исключительную привязку порта")
            self.socket.setsockopt(socket.SOL_SOCKET, option, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        try:
            connection.settimeout(60)
            return connection, _peer_address(address)
        except Exception:
            connection.close()
            raise


class _TLSWebServer(_WebServer):
    def __init__(self, address, handler, certificates):
        self.certificates = certificates
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        try:
            context = self.certificates.context()
            if context is None:
                raise ssl.SSLError("HTTPS-сертификат недоступен или просрочен")
            connection = context.wrap_socket(connection, server_side=True,
                                             do_handshake_on_connect=False)
            connection.settimeout(10)
            return connection, address
        except Exception:
            connection.close()
            raise

    def finish_request(self, request, client_address):
        try:
            request.do_handshake()
            request.settimeout(60)
        except (OSError, ssl.SSLError):
            return
        # Общий process_request_thread закроет сокет и освободит слот
        # также при отказе handshake или исключении самого handler.
        super().finish_request(request, client_address)


class _IPv6Binding:
    address_family = socket.AF_INET6

    def server_bind(self):
        # Отдельный сервер IPv4 исключает двойной приём IPv4 на этом сокете.
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class _IPv6WebServer(_IPv6Binding, _WebServer):
    pass


class _IPv6TLSWebServer(_IPv6Binding, _TLSWebServer):
    pass


def create_listener(family: int, address: tuple, handler,
                    certificates=None) -> ThreadingHTTPServer:
    """Создать сервер одной привязки; её ошибки обрабатывает транспортный слой."""
    if family == socket.AF_INET:
        server_class = _TLSWebServer if certificates is not None else _WebServer
    elif family == socket.AF_INET6:
        server_class = _IPv6TLSWebServer if certificates is not None else _IPv6WebServer
    else:
        raise ValueError("Поддерживаются только привязки IPv4 и IPv6")
    if certificates is not None:
        return server_class(address, handler, certificates)
    return server_class(address, handler)
