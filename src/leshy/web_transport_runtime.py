"""HTTP/HTTPS на сетевых интерфейсах используют один handler и экземпляр Лешего."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from http.server import ThreadingHTTPServer
import errno
import ipaddress
import os
from pathlib import Path
import socket
import threading
from typing import Any, Callable

from .diagnostics import problem
from .web_certificates import WebCertificateStore, covers_hostname
from .web_listeners import binding_addresses, create_listener
from .web_transport_settings import WebTransportSettings, validate_settings


_listener_callback: ContextVar[Callable[[str], None] | None] = ContextVar("leshy_listener_callback", default=None)


@contextmanager
def listener_ready(callback: Callable[[str], None] | None):
    """Передать launcher-уведомление через существующую цепочку serve без глобальной гонки."""
    token = _listener_callback.set(callback)
    try:
        yield
    finally:
        _listener_callback.reset(token)


class WebTransportRuntime:
    def __init__(self, brain_dir: Path | str, host: str, explicit_http_port: int | None = None):
        self.settings = WebTransportSettings(brain_dir)
        self.configuration = self.settings.effective(explicit_http_port)
        self._http_override = explicit_http_port is not None or "LESHY_PORT" in os.environ
        self._https_override = "LESHY_HTTPS_PORT" in os.environ
        self.host = host
        self._on_listening = _listener_callback.get()
        self.lock = threading.RLock()
        self.certificates = WebCertificateStore(brain_dir, lambda: self.settings.load()["hostname"])
        self._servers: dict[str, ThreadingHTTPServer] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._bindings: dict[str, dict[str, Any]] = {}
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
        self.acme = WebAcmeService(brain_dir, self.install_certificate, self._http01_available)

    def _http01_available(self) -> bool:
        with self.lock:
            return bool(self.settings.load()["http_enabled"] and self._states["http"]["listening"])

    def listening_url(self) -> str | None:
        """Только реально запущенный listener; не открывать чужой сервис на занятом порте."""
        with self.lock:
            for protocol in ("http", "https"):
                if not self._states[protocol]["listening"]:
                    continue
                for name, thread in self._threads.items():
                    if self._protocol(name) != protocol or not thread.is_alive():
                        continue
                    binding = self._bindings.get(name, self._states[protocol])
                    if not binding["listening"]:
                        continue
                    if protocol == "https":
                        host = self.configuration["hostname"]
                        if (not host or self.certificates.context() is None
                                or not covers_hostname(self.certificates.status(), host)):
                            continue
                    else:
                        host = binding.get("address", self.host)
                        host = "127.0.0.1" if host in {"", "auto", "0.0.0.0"} else "::1" if host == "::" else host
                    if ":" in host:
                        host = "[" + host.replace("%", "%25") + "]"
                    return f"{protocol}://{host}:{binding['port']}/"
            return None

    def cluster_profile(self, local_ipv4: str) -> dict:
        """Кандидат endpoint по живым привязкам; внешняя достижимость не доказана."""
        unavailable = {"endpoint": None, "accepts_remote": False,
                       "reason": "Нет пригодного входящего HTTP/HTTPS endpoint; проверьте адрес, SAN или явный advertise",
                       "reachability_verified": False}
        with self.lock:
            for protocol in ("http", "https"):
                if not self._states[protocol]["listening"]:
                    continue
                for name, thread in self._threads.items():
                    if self._protocol(name) != protocol or not thread.is_alive():
                        continue
                    binding = self._bindings.get(name)
                    if not binding or not binding["listening"]:
                        continue
                    try:
                        address = ipaddress.ip_address(binding["address"].split("%", 1)[0])
                    except ValueError:
                        continue
                    if address.is_loopback:
                        continue
                    if protocol == "https":
                        host = self.configuration["hostname"]
                        if (not host or host == "localhost" or host.endswith(".localhost")
                                or self.certificates.context() is None
                                or not covers_hostname(self.certificates.status(), host)):
                            continue
                        try:
                            target = ipaddress.ip_address(host)
                            if target.is_loopback or target.is_unspecified or (target.version == 6 and target.is_link_local):
                                continue
                        except ValueError:
                            pass
                    else:
                        if address.is_unspecified:
                            if address.version != 4:
                                continue  # Не изобретать удалённый IPv6 адрес из wildcard ::.
                            try:
                                address = ipaddress.IPv4Address(local_ipv4)
                            except ValueError:
                                continue
                        if address.is_loopback or address.is_unspecified or (address.version == 6 and address.is_link_local):
                            continue
                        host = str(address)
                    if ":" in host:
                        host = "[" + host.replace("%", "%25") + "]"
                    return {"endpoint": f"{protocol}://{host}:{binding['port']}", "accepts_remote": True,
                            "reason": "Кандидат по работающему listener; DNS/сеть и доверие CA у соседей не проверены",
                            "reachability_verified": False}
        return unavailable

    @staticmethod
    def _protocol(name: str) -> str:
        return name.split("/", 1)[0]

    def _refresh_protocol(self, protocol: str) -> None:
        """Под self.lock; каждое семейство сохраняет собственный фактический результат."""
        bindings = [item for name, item in self._bindings.items() if self._protocol(name) == protocol]
        if not bindings:
            return
        listening = any(item["listening"] for item in bindings)
        bound = any(item["status"] == "bound" for item in bindings)
        stopped = any(item["status"] == "stopped" for item in bindings)
        state = "listening" if listening else "bound" if bound else "stopped" if stopped else "bind_failed"
        self._states[protocol].update(listening=listening, status=state,
                                     error="" if listening or bound else "Нет доступной привязки " + protocol.upper())

    def _bind(self, protocol: str) -> None:
        if (any(self._protocol(name) == protocol for name in self._servers)
                or not self.configuration[protocol + "_enabled"]):
            return
        if protocol == "https" and self.certificates.context() is None:
            self._states[protocol].update(status="waiting_certificate", listening=False,
                                         error="Загрузите действующий сертификат или получите его через ACME")
            return
        candidates = binding_addresses(self.host, self.configuration[protocol + "_port"])
        last_error = None
        for index, (family, address) in enumerate(candidates):
            name = protocol if index == 0 else protocol + "/" + str(index)
            binding = {"family": "IPv6" if family == socket.AF_INET6 else "IPv4",
                       "address": address[0], "port": address[1], "listening": False, "status": "not_started"}
            self._bindings[name] = binding
            try:
                server = create_listener(family, address, self._handler,
                                         self.certificates if protocol == "https" else None)
            except OSError as exc:
                last_error = exc
                unavailable = exc.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.ENOPROTOOPT,
                                           10047, 10043, 10042}
                binding.update(status="unavailable" if unavailable else "bind_failed",
                               error="Семейство адресов недоступно" if unavailable else "Адрес или порт занят либо недоступен")
                continue
            self._servers[name] = server
            binding.update(address=server.server_address[0], port=server.server_address[1], status="bound")
            if len(server.server_address) > 3 and server.server_address[3] and "%" not in binding["address"]:
                binding["address"] += "%" + str(server.server_address[3])
            self._states[protocol]["port"] = server.server_address[1]
        self._refresh_protocol(protocol)
        if not any(self._protocol(name) == protocol for name in self._servers):
            if last_error is not None:
                raise last_error
            raise OSError("Не найдены подходящие адреса для привязки")

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
                    raise RuntimeError("Ни одна HTTP/HTTPS привязка недоступна; проверьте адреса, порты и сертификат")
            except BaseException:
                for server in self._servers.values():
                    server.server_close()
                self._servers.clear()
                raise

    def _start_server(self, name: str) -> None:
        if name in self._threads:
            return
        server = self._servers[name]
        protocol = self._protocol(name)
        binding = self._bindings[name]

        def run() -> None:
            try:
                server.serve_forever(poll_interval=0.25)
            except Exception as exc:
                problem("web_listener_failed", type(exc).__name__, protocol=name)
            finally:
                with self.lock:
                    binding.update(listening=False, status="stopped")
                    self._refresh_protocol(protocol)

        thread = threading.Thread(target=run, name="leshy-" + name, daemon=True)
        self._threads[name] = thread
        try:
            thread.start()
        except BaseException:
            self._threads.pop(name, None)
            server.server_close()
            self._servers.pop(name, None)
            binding.update(listening=False, status="start_failed", error="Не удалось запустить обработчик")
            self._refresh_protocol(protocol)
            raise
        binding.update(listening=True, status="listening")
        self._refresh_protocol(protocol)

    def serve_forever(self) -> None:
        try:
            self._serve_forever()
        finally:
            with self.lock:
                self._running = False

    def _serve_forever(self) -> None:
        with self.lock:
            if self._stopping.is_set():
                return
            self._running = True
            for name in list(self._servers):
                if self._stopping.is_set():
                    break
                try:
                    self._start_server(name)
                except (OSError, RuntimeError) as exc:
                    problem("web_listener_start_failed", type(exc).__name__, protocol=self._protocol(name))
            if self._stopping.is_set():
                return
            if not any(thread.is_alive() for thread in self._threads.values()):
                raise RuntimeError("Не запущен ни один HTTP/HTTPS обработчик")
        self.acme.start()
        if self._on_listening:
            callback, self._on_listening = self._on_listening, None
            url = self.listening_url()
            if url:
                try:
                    callback(url)
                except Exception as exc:
                    problem("web_browser_open_failed", type(exc).__name__)
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
            if (self._running and self.configuration["https_enabled"]
                    and not any(self._protocol(name) == "https" for name in self._servers)):
                try:
                    self._bind("https")
                    for name in list(self._servers):
                        if self._protocol(name) == "https":
                            try:
                                self._start_server(name)
                            except (OSError, RuntimeError):
                                pass
                    if not self._states["https"]["listening"]:
                        raise RuntimeError("HTTPS не запущен")
                except (OSError, RuntimeError):
                    self._states["https"].update(status="bind_failed", listening=False,
                                                 error="Сертификат сохранён; порт HTTPS занят или недоступен")
            value["active_for_new_connections"] = bool(self._states["https"]["listening"])
            return value

    def save_settings(self, body: dict) -> dict:
        # Единый порядок ACME → core не допускает гонку HTTP-off с configure/renewal.
        with self.acme.configuration_guard() as acme:
            return self._save_settings(body, acme)

    def _save_settings(self, body: dict, acme: dict) -> dict:
        with self.lock:
            prospective = validate_settings({**self.settings.load(), **body})
            effective = dict(prospective)
            if self._http_override:
                effective["http_port"] = self.configuration["http_port"]
            if self._https_override:
                effective["https_port"] = self.configuration["https_port"]
            try:
                validate_settings(effective)
            except ValueError as exc:
                raise ValueError("С учётом портов, заданных при запуске: " + str(exc)) from exc
            if not prospective["http_enabled"]:
                if acme["in_progress"] or (acme["enabled"] and acme["auto_renew"]):
                    raise ValueError("Перед выключением HTTP завершите выпуск и отключите автопродление HTTP-01")
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
            for protocol, state in states.items():
                state["bindings"] = [dict(binding) for name, binding in self._bindings.items()
                                     if self._protocol(name) == protocol]
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

    def shutdown(self) -> None:
        """Совместимый сигнал Windows tray; cleanup выполняет основной serve/finally."""
        self._stopping.set()

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
            for binding in self._bindings.values():
                binding.update(listening=False, status="stopped")
