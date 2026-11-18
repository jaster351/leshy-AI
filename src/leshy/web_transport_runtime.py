"""Два HTTP/HTTPS listener используют один обработчик и один экземпляр Лешего."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import socket
import ssl
import threading
from typing import Any

from .diagnostics import problem
from .web_certificates import WebCertificateStore, covers_hostname
from .web_transport_settings import WebTransportSettings, validate_settings


class _WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(60)
        return connection, address


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
            connection = context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False)
            connection.settimeout(10)
            return connection, address
        except Exception:
            connection.close()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.do_handshake()
            request.settimeout(60)
        except (OSError, ssl.SSLError):
            self.shutdown_request(request)
            return
        super().process_request_thread(request, client_address)


class WebTransportRuntime:
    def __init__(self, brain_dir: Path | str, host: str, explicit_http_port: int | None = None):
        self.settings = WebTransportSettings(brain_dir)
        self.configuration = self.settings.effective(explicit_http_port)
        self._http_override = explicit_http_port is not None or "LESHY_PORT" in os.environ
        self._https_override = "LESHY_HTTPS_PORT" in os.environ
        self.host = host
        self.lock = threading.RLock()
        self.certificates = WebCertificateStore(brain_dir, lambda: self.settings.load()["hostname"])
        self._servers: dict[str, _WebServer] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._states: dict[str, dict[str, Any]] = {
            name: {"enabled": self.configuration[name + "_enabled"],
                   "port": self.configuration[name + "_port"], "listening": False,
                   "status": "not_started" if self.configuration[name + "_enabled"] else "disabled"}
            for name in ("http", "https")
        }
        self._handler = None
        self._running = False
        self._stopping = threading.Event()
        from .web_acme import WebAcmeService
        self.acme = WebAcmeService(brain_dir, self.install_certificate)

    def _bind(self, protocol: str) -> None:
        if protocol in self._servers or not self.configuration[protocol + "_enabled"]:
            return
        if protocol == "https" and self.certificates.context() is None:
            self._states[protocol].update(status="waiting_certificate", listening=False,
                                         error="Загрузите действующий сертификат или получите его через ACME")
            return
        base = _TLSWebServer if protocol == "https" else _WebServer
        server_class = type("IPv6WebServer", (base,), {"address_family": socket.AF_INET6}) if ":" in self.host else base
        address = (self.host, self.configuration[protocol + "_port"])
        server = (server_class(address, self._handler, self.certificates) if protocol == "https"
                  else server_class(address, self._handler))
        self._servers[protocol] = server
        self._states[protocol].update(status="bound", port=server.server_address[1], listening=False, error="")

    def bind(self, handler) -> None:
        with self.lock:
            self._handler = handler
            try:
                for name in ("http", "https"):
                    try:
                        self._bind(name)
                    except OSError:
                        self._states[name].update(status="bind_failed", listening=False,
                                                  error="Порт " + name.upper() + " занят или недоступен")
                if not self._servers:
                    raise RuntimeError("Нет доступного listener: для режима только HTTPS нужен действующий сертификат")
            except BaseException:
                for server in self._servers.values():
                    server.server_close()
                self._servers.clear()
                raise

    def _start_server(self, name: str) -> None:
        if name in self._threads:
            return
        server = self._servers[name]

        def run() -> None:
            try:
                server.serve_forever(poll_interval=0.25)
            except Exception as exc:
                problem("web_listener_failed", type(exc).__name__, protocol=name)
            finally:
                with self.lock:
                    self._states[name].update(listening=False, status="stopped")

        thread = threading.Thread(target=run, name="leshy-" + name, daemon=True)
        self._threads[name] = thread
        try:
            thread.start()
        except BaseException:
            self._threads.pop(name, None)
            server.server_close()
            self._servers.pop(name, None)
            self._states[name].update(listening=False, status="start_failed")
            raise
        self._states[name].update(listening=True, status="listening")

    def serve_forever(self) -> None:
        with self.lock:
            self._running = True
            for name in list(self._servers):
                self._start_server(name)
        self.acme.start()
        while not self._stopping.wait(0.5):
            with self.lock:
                if not any(thread.is_alive() for thread in self._threads.values()):
                    break

    def install_certificate(self, certificate_pem: str, private_key_pem: str,
                            password: str | None = None) -> dict:
        with self.lock:
            return self.install_prepared_certificate(self.prepare_certificate(certificate_pem, private_key_pem, password))

    def prepare_certificate(self, certificate_pem: str, private_key_pem: str,
                            password: str | None = None):
        with self.lock:
            return self.certificates.prepare(certificate_pem, private_key_pem, password)

    def import_certificate(self, certificate_pem: str, private_key_pem: str,
                           password: str | None = None) -> dict:
        prepared = self.prepare_certificate(certificate_pem, private_key_pem, password)
        return self.acme.replace_certificate_manually(lambda: self.install_prepared_certificate(prepared))

    def install_prepared_certificate(self, prepared) -> dict:
        with self.lock:
            if self._stopping.is_set():
                raise RuntimeError("Сервер останавливается; сертификат не изменён")
            value = self.certificates.install_prepared(prepared)
            # Новые TLS-соединения получают новый контекст. Существующие не обрываем.
            if self._running and self.configuration["https_enabled"] and "https" not in self._servers:
                try:
                    self._bind("https")
                    self._start_server("https")
                except (OSError, RuntimeError):
                    self._states["https"].update(status="bind_failed", listening=False,
                                                 error="Сертификат сохранён; порт HTTPS занят или недоступен")
            value["active_for_new_connections"] = bool(self._states["https"]["listening"])
            return value

    def save_settings(self, body: dict) -> dict:
        with self.lock:
            prospective = validate_settings({**self.settings.load(), **body})
            if not prospective["http_enabled"]:
                if (self.certificates.context() is None
                        or not covers_hostname(self.certificates.status(), prospective["hostname"])):
                    raise ValueError("Перед выключением HTTP загрузите действующий сертификат для указанного имени сервера")
            value = self.settings.save(body)
            return {"settings": value, "restart_required": value != self.configuration}

    def status(self) -> dict:
        # Не вызывать ACME под core-lock: выдача сертификата берёт ACME → core.
        with self.lock:
            value = self.settings.load()
            states = {name: dict(state) for name, state in self._states.items()}
            certificate = self.certificates.status()
            certificate["hostname_matches_settings"] = covers_hostname(certificate, value["hostname"])
            certificate["active_for_new_connections"] = bool(states["https"]["listening"] and certificate.get("valid"))
            runtime = {**states, "host": self.host, "restart_required": value != self.configuration,
                       "applied_settings": dict(self.configuration),
                       "http_port_override": self._http_override,
                       "https_port_override": self._https_override}
        return {"settings": value, "runtime": runtime, "certificate": certificate,
                "acme": self.acme.status(),
                "capabilities": {"configure": True, "import_certificate": True,
                                 "acme": True, "browser_trust_verified": False}}

    def close(self) -> None:
        self._stopping.set()
        try:
            self.acme.stop()
        except Exception as exc:
            problem("web_acme_shutdown_failed", type(exc).__name__)
        with self.lock:
            servers = list(self._servers.items())
        for name, server in servers:
            thread = self._threads.get(name)
            if thread and thread.is_alive():
                server.shutdown()
            server.server_close()
        for thread in list(self._threads.values()):
            if thread.is_alive():
                thread.join(timeout=5)
        with self.lock:
            self._running = False
            for state in self._states.values():
                state.update(listening=False, status="stopped")
