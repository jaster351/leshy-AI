"""Восемь изолированных проверок готовности и ограничений HTTP/HTTPS.

Подготовка исходника не разрешает его запуск: требуется отдельный допуск владельца.
После переноса в tests/ читаются только выбранные AST-определения src/leshy.
Сокеты, потоки, часы, listeners и TLS заменены; пользовательские данные,
приложение, модели, CA и доверие ОС не создаются и не изменяются.
"""
from __future__ import annotations

import ast
import builtins
from contextlib import ExitStack
import copy
import __future__ as future_flags
import hashlib
import hmac
from http import HTTPStatus
from email.message import Message
import http.client
import io
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


SOURCE = Path(__file__).resolve().parents[1] / "src" / "leshy"
SECRET = b"s" * 32
NONCE = "ab" * 32
PIN_BYTES = b"synthetic-public-leaf"
PIN = hashlib.sha256(PIN_BYTES).hexdigest()
HOST = "secure.example.invalid"


def denied(*args, **kwargs):
    raise AssertionError("Реальные импорты приложения, сеть, потоки и записи запрещены")


def namespace(**values):
    return {"__name__": "_isolated_web_readiness_limits",
            "__builtins__": dict(vars(builtins), __import__=denied, open=denied), **values}


def definitions(filename, names, scope):
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8-sig"), filename)
    selected = []
    found = set()
    for node in tree.body:
        identifiers = {node.name} if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else set()
        if isinstance(node, ast.Assign):
            identifiers = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if identifiers & set(names):
            selected.append(copy.deepcopy(node))
            found.update(identifiers & set(names))
    if found != set(names):
        raise AssertionError(f"Изменился AST-контракт {filename}: {set(names) - found}")
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, filename, "exec", flags=future_flags.annotations.compiler_flag,
                 dont_inherit=True), scope)
    return scope


def selected_class(filename, name, methods, scope):
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8-sig"), filename)
    node = copy.deepcopy(next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == name))
    node.body = [item for item in node.body if isinstance(item, ast.FunctionDef) and item.name in methods]
    if {item.name for item in node.body} != set(methods):
        raise AssertionError(f"Изменился набор методов {name}")
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, filename, "exec", flags=future_flags.annotations.compiler_flag,
                 dont_inherit=True), scope)
    return scope[name]


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value


class Flag:
    def __init__(self):
        self.value = False

    def is_set(self):
        return self.value

    def set(self):
        self.value = True


class Lock:
    def __init__(self):
        self.depth = 0

    def __enter__(self):
        self.depth += 1
        return self

    def __exit__(self, *args):
        self.depth -= 1


class Slots:
    def __init__(self, value):
        self.maximum = self.value = value
        self.acquisitions = []

    def acquire(self, blocking=True):
        self.acquisitions.append(blocking)
        if not self.value:
            return False
        self.value -= 1
        return True

    def release(self):
        if self.value >= self.maximum:
            raise ValueError("Лишнее освобождение тестового слота")
        self.value += 1


class Connection:
    def __init__(self):
        self.timeout = 60.0
        self.timeouts, self.handshakes = [], 0
        self.failure = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value
        self.timeouts.append(value)

    def gettimeout(self):
        return self.timeout

    def do_handshake(self):
        self.handshakes += 1
        if self.failure:
            raise self.failure

    def close(self):
        self.closed = True


class WorkerFixture:
    def __init__(self):
        class Parent:
            def __init__(self, address, handler):
                self.handler = handler
                self.pending, self.closed, self.errors, self.served = [], [], [], []
                self.fail_start = False
                self.incoming = (Connection(), ("192.0.2.20", 5000))

            def process_request(self, request, address):
                if self.fail_start:
                    raise RuntimeError("Имитация ошибки запуска worker")
                self.pending.append((request, address))

            def process_request_thread(self, request, address):
                try:
                    self.finish_request(request, address)
                except Exception as exc:
                    self.errors.append(exc)
                finally:
                    self.shutdown_request(request)

            def finish_request(self, request, address):
                self.served.append(request)
                self.handler(request, address, self)

            def shutdown_request(self, request):
                self.closed.append(request)
                request.close()

            def get_request(self):
                return self.incoming

        scope = namespace(ThreadingHTTPServer=Parent, threading=SimpleNamespace(BoundedSemaphore=Slots),
                          os=SimpleNamespace(name="posix"), socket=socket, ssl=ssl, ipaddress=ipaddress)
        definitions("web_listeners.py", {"_peer_address", "_WebServer", "_TLSWebServer"}, scope)
        self.http, self.https = scope["_WebServer"], scope["_TLSWebServer"]
        self.wraps = []

    def server(self, tls=False, handler=lambda *args: None):
        if not tls:
            return self.http(("192.0.2.10", 8765), handler)

        def wrap(request, **options):
            self.wraps.append(options)
            return request

        certificates = SimpleNamespace(context=lambda: SimpleNamespace(wrap_socket=wrap))
        return self.https(("192.0.2.10", 8766), handler, certificates)


class Chunks:
    def __init__(self, clock, chunks):
        self.clock, self.chunks = clock, list(chunks)
        self.reads = []

    def read1(self, size):
        self.reads.append(size)
        if not self.chunks:
            return b""
        delay, value = self.chunks.pop(0)
        self.clock.value += delay
        if isinstance(value, BaseException):
            raise value
        part = value[:size]
        if len(value) > size:
            self.chunks.insert(0, (0.0, value[size:]))
        return part


class HandlerFixture:
    def __init__(self):
        self.clock, self.mutations, self.status_reads = Clock(), [], []

        class Parent:
            def _json(self, status, body):
                self.response, self.json_body = status, body

            def send_response(self, status):
                self.response = status

            def send_header(self, name, value):
                self.sent_headers.append((name, value))

            def end_headers(self):
                pass

            def do_GET(self):
                self.parent_get = True

            def do_POST(self):
                self.parent_post = True

        self.service = SimpleNamespace(
            save_settings=lambda value: self.mutations.append(value),
            status=lambda: self.status_reads.append(True) or {"settings": {}})
        self.scope = namespace(HTTPStatus=HTTPStatus, hashlib=hashlib, hmac=hmac, ipaddress=ipaddress, json=json,
                               os=SimpleNamespace(getenv=lambda *args: ""), re=re, ssl=ssl,
                               time=self.clock, urlsplit=urlsplit, problem=lambda *args, **kwargs: None,
                               _PREVIOUS_MAKE_HANDLER=lambda *args: Parent)
        definitions("web_readiness.py", {"_HEX", "READINESS_PATH", "response_proof"}, self.scope)
        definitions("web_server_impl18.py", {"_PREFIX", "_VOICE_PATHS", "_MAX_JSON",
                    "_BODY_DEADLINE_SECONDS", "_SecurityBodyTimeout", "_origin_tuple",
                    "_unambiguous_object", "make_handler"}, self.scope)
        app = SimpleNamespace(web_security=self.service, owner_identity=SimpleNamespace(cookie_name="owner"))
        self.handler_class = self.scope["make_handler"](app, None, None, None)

    def handler(self, raw=b'{"x":1}', *, chunks=None, pairs=None):
        value = self.handler_class.__new__(self.handler_class)
        value.headers = Message()
        defaults = [("Content-Type", "application/json"), ("Content-Length", str(len(raw)))]
        for name, content in defaults if pairs is None else pairs:
            value.headers[name] = content
        value.path = "/v1/web-security/settings"
        value.connection = Connection()
        value.client_address = ("192.0.2.20", 5000)
        value.rfile = Chunks(self.clock, [(0.0, raw)] if chunks is None else chunks)
        value.wfile, value.sent_headers = io.BytesIO(), []
        value.server = SimpleNamespace(_leshy_readiness_secret=SECRET)
        value.response, value.json_body, value.close_connection = None, None, False
        value._web_security_owner = lambda write=False: True
        return value


class RawStream(io.RawIOBase):
    def __init__(self, owner):
        self.owner = owner

    def readable(self):
        return True

    def readinto(self, buffer):
        value = self.owner.reader.read1(len(buffer))
        buffer[:len(value)] = value
        return len(value)


class WireSocket(Connection):
    def __init__(self, clock, chunks):
        super().__init__()
        self.reader, self.connected, self.sent = Chunks(clock, chunks), [], []

    def connect(self, target):
        ipaddress.ip_address(target[0])
        self.connected.append(target)

    def makefile(self, mode, buffering=0):
        if mode != "rb" or buffering != 0:
            raise AssertionError("Ожидался только неподключённый синтетический поток чтения")
        return RawStream(self)

    def sendall(self, content):
        self.sent.append(bytes(content))

    def getpeercert(self, binary_form=False):
        if not binary_form:
            raise AssertionError("Ожидался DER leaf для отпечатка")
        return PIN_BYTES


class TLSContext:
    def __init__(self):
        self.verify_mode, self.check_hostname = ssl.CERT_REQUIRED, True
        self.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
        self.wraps, self.failure = [], None

    def wrap_socket(self, raw, *, server_hostname):
        self.wraps.append(server_hostname)
        if self.failure:
            raise self.failure
        return raw


class ProbeFixture:
    def __init__(self, *, body=None, headers=None, chunks=None, status=200):
        self.clock, self.created = Clock(), []
        proof = hmac.new(SECRET, NONCE.encode("ascii"), hashlib.sha256).hexdigest()
        body = json.dumps({"proof": proof}, separators=(",", ":")).encode("ascii") if body is None else body
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))] if headers is None else headers
        wire = (f"HTTP/1.1 {status} synthetic\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers)
                + "Connection: close\r\n\r\n").encode("ascii") + body
        self.sock = WireSocket(self.clock, [(0.0, wire)] if chunks is None else chunks)

        def create(family, kind):
            if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
                raise AssertionError("Неожиданный тип синтетического сокета")
            self.created.append((family, kind))
            return self.sock

        self.scope = namespace(http=http, io=io, time=self.clock, hashlib=hashlib, hmac=hmac,
                               ipaddress=ipaddress, json=json, re=re, ssl=ssl,
                               secrets=SimpleNamespace(token_hex=lambda size: NONCE if size == 32 else denied()),
                               socket=SimpleNamespace(socket=create, AF_INET=socket.AF_INET,
                                                      AF_INET6=socket.AF_INET6, SOCK_STREAM=socket.SOCK_STREAM,
                                                      getaddrinfo=denied, create_connection=denied))
        definitions("web_acme.py", {"_DeadlineReader", "_DeadlineSocket", "_DeadlineHTTPResponse"}, self.scope)
        definitions("web_readiness.py", {"_HEX", "READINESS_PATH", "response_proof", "_target", "probe_listener"}, self.scope)

    def probe(self, **changes):
        options = dict(family=socket.AF_INET, address=("192.0.2.10", 8765), secret=SECRET,
                       tls_context=None, server_hostname="", fingerprint_sha256="",
                       deadline=10.0, stopped=lambda: False)
        options.update(changes)
        return self.scope["probe_listener"](**options)


class RuntimeFixture:
    def __init__(self, tls=False):
        self.clock, self.calls, self.probes = Clock(), [], []
        self.behavior = lambda **options: False

        def probe(**options):
            if self.runtime.lock.depth:
                raise AssertionError("Probe вызван под core lock")
            self.probes.append(options)
            return self.behavior(**options)

        scope = namespace(time=self.clock, socket=socket, probe_listener=probe,
                          covers_hostname=lambda metadata, host: host in metadata.get("dns_names", []),
                          problem=lambda *args, **kwargs: None)
        cls = selected_class("web_transport_runtime.py", "WebTransportRuntime",
                             {"_protocol", "_refresh_protocol", "listening_url", "_verify_listeners",
                              "_notify_ready", "install_prepared_certificate"}, scope)
        value = self.runtime = cls.__new__(cls)
        value.lock, value._stopping = Lock(), Flag()
        value.host, value._running = "192.0.2.10", True
        self.name = "https" if tls else "http"
        self.server = SimpleNamespace(address_family=socket.AF_INET,
                                      server_address=("192.0.2.10", 8766 if tls else 8765),
                                      _leshy_readiness_secret=SECRET, _leshy_failed=Flag())
        self.thread = SimpleNamespace(alive=True)
        self.thread.is_alive = lambda: self.thread.alive
        value._servers, value._threads = {self.name: self.server}, {self.name: self.thread}
        value._bindings = {self.name: {"listening": True, "ready": False, "status": "listening",
                                       "family": "IPv4", "address": "192.0.2.10",
                                       "port": 8766 if tls else 8765}}
        value._states = {name: {"listening": name == self.name, "ready": False,
                               "status": "listening" if name == self.name else "not_started"}
                         for name in ("http", "https")}
        value.configuration = {"hostname": HOST, "http_enabled": not tls, "https_enabled": tls}
        value._on_listening = self.calls.append
        self.certificate = SimpleNamespace(pin=PIN, alive=True, installs=[])
        self.certificate.context = lambda: object() if self.certificate.alive else None
        self.certificate.status = lambda: {"fingerprint_sha256": self.certificate.pin, "dns_names": [HOST]}
        self.certificate.readiness_tls_snapshot = lambda host: (object(), self.certificate.pin, host or HOST)
        self.certificate.install_prepared = lambda prepared: self.certificate.installs.append(prepared) or {"valid": True}
        value.certificates = self.certificate


class WebReadinessLimitTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        original_socket = socket.socket
        for method in ("connect", "bind", "sendto"):
            self.stack.enter_context(patch.object(original_socket, method, side_effect=denied))
        for name in ("socket", "create_connection", "getaddrinfo"):
            self.stack.enter_context(patch.object(socket, name, side_effect=denied))
        self.stack.enter_context(patch.object(threading.Thread, "start", side_effect=denied))
        self.stack.enter_context(patch.object(http.client.HTTPConnection, "connect", side_effect=denied))

    def test_worker_slots_are_nonblocking_held_until_handler_end_and_recovered_after_errors(self):
        for tls in (False, True):
            with self.subTest(tls=tls):
                fixture = WorkerFixture()
                seen = []

                def handler(request, address, server):
                    seen.append(server._worker_slots.value)
                    self.assertEqual(server._worker_slots.value, 0)

                server = fixture.server(tls=tls, handler=handler)
                self.assertEqual(server.max_connection_workers, 32)
                requests = [Connection() for _ in range(33)]
                for request in requests:
                    server.process_request(request, ("192.0.2.20", 5000))
                self.assertEqual(len(server.pending), 32)
                self.assertEqual(server._worker_slots.value, 0)
                self.assertEqual(server.closed, [requests[-1]])
                self.assertTrue(all(flag is False for flag in server._worker_slots.acquisitions))
                request, address = server.pending.pop(0)
                server.process_request_thread(request, address)
                self.assertEqual(seen, [0])
                self.assertEqual(server._worker_slots.value, 1)
                self.assertTrue(request.closed)
                # Остальные callback выполняются синхронно, без создания потоков.
                server.handler = lambda *args: None
                for request, address in server.pending:
                    server.process_request_thread(request, address)
                self.assertEqual(server._worker_slots.value, 32)
                server.fail_start = True
                with self.assertRaises(RuntimeError):
                    server.process_request(Connection(), ("192.0.2.20", 5000))
                self.assertEqual(server._worker_slots.value, 32)
                server.fail_start = False

                def broken(*args):
                    raise RuntimeError("Имитация ошибки handler")

                server.handler = broken
                request = Connection()
                server.process_request(request, ("192.0.2.20", 5000))
                server.process_request_thread(request, ("192.0.2.20", 5000))
                self.assertEqual(server._worker_slots.value, 32)
                self.assertEqual(len(server.errors), 1)
                self.assertTrue(request.closed)

    def test_tls_handshake_runs_in_worker_and_failed_or_stalled_handshake_releases_slot(self):
        for failure in (None, TimeoutError("Имитация зависшего TLS"), ssl.SSLError("Имитация ошибки TLS")):
            with self.subTest(failure=type(failure).__name__):
                fixture = WorkerFixture()
                server = fixture.server(tls=True)
                request, address = server.get_request()
                self.assertEqual(request.handshakes, 0)
                self.assertEqual(request.timeout, 10)
                self.assertEqual(fixture.wraps, [{"server_side": True, "do_handshake_on_connect": False}])
                request.failure = failure
                server.process_request(request, address)
                self.assertEqual(request.handshakes, 0)
                self.assertEqual(server._worker_slots.value, 31)
                server.process_request_thread(request, address)
                self.assertEqual(request.handshakes, 1)
                self.assertEqual(server._worker_slots.value, 32)
                self.assertTrue(request.closed)
                self.assertEqual(server.served, [request] if failure is None else [])
                self.assertEqual(request.timeout, 60 if failure is None else 10)

    def test_body_uses_one_absolute_deadline_returns_408_restores_timeout_and_never_mutates(self):
        for chunks in ([(6.0, b'{"x":'), (5.0, b'1}')], [(0.0, TimeoutError("Имитация timeout"))]):
            with self.subTest(chunks=repr(chunks)):
                fixture = HandlerFixture()
                handler = fixture.handler(chunks=chunks)
                handler.do_POST()
                self.assertEqual(handler.response, HTTPStatus.REQUEST_TIMEOUT)
                self.assertTrue(handler.close_connection)
                self.assertEqual(handler.connection.timeout, 60)
                self.assertEqual(handler.connection.timeouts[-1], 60)
                self.assertEqual(fixture.mutations, [])
                self.assertEqual(fixture.status_reads, [])
                budgets = handler.connection.timeouts[:-1]
                self.assertTrue(all(0 < value <= 10 for value in budgets))
                self.assertEqual(budgets, sorted(budgets, reverse=True))

    def test_body_rejects_bad_framing_eof_oversize_or_json_and_accepts_split_utf8(self):
        cases = [
            (b'{"x":1}', [("Content-Type", "application/json"), ("Content-Length", "+7")], None),
            (b'{"x":1}', [("Content-Type", "application/json"), ("Content-Length", "524289")], None),
            (b'{"x":1}', [("Content-Type", "application/json"), ("Content-Length", "7"), ("Content-Length", "7")], None),
            (b'{"x":1}', [("Content-Type", "application/json"), ("Content-Length", "7"), ("Transfer-Encoding", "chunked")], None),
            (b'{"x":1}', [("Content-Type", "text/plain"), ("Content-Length", "7")], None),
            (b'{"x":1}', None, [(0.0, b'{"x":')]),
            (b'{"x":1,"x":2}', None, None), (b'[]', None, None), (b'\xff', None, None),
        ]
        for raw, pairs, chunks in cases:
            with self.subTest(raw=raw, pairs=pairs):
                fixture = HandlerFixture()
                handler = fixture.handler(raw, pairs=pairs, chunks=chunks)
                with self.assertRaises(ValueError):
                    handler._security_body()
                self.assertEqual(fixture.mutations, [])
                self.assertEqual(handler.connection.timeout, 60)
                if pairs is not None:
                    self.assertEqual(handler.rfile.reads, [])
                    self.assertTrue(handler.close_connection)
                if chunks is not None:
                    self.assertTrue(handler.close_connection)
        fixture = HandlerFixture()
        raw = json.dumps({"текст": "Леший"}, ensure_ascii=False).encode("utf-8")
        handler = fixture.handler(raw, chunks=[(4.0, raw[:3]), (4.0, raw[3:])])
        self.assertEqual(handler._security_body(), {"текст": "Леший"})
        self.assertEqual(handler.connection.timeout, 60)
        self.assertFalse(handler.close_connection)
        self.assertLessEqual(max(handler.rfile.reads), 16 * 1024)
        self.assertEqual(handler.connection.timeouts, [10.0, 6.0, 60.0])

    def test_readiness_endpoint_returns_own_hmac_and_rejects_missing_duplicate_or_bad_nonce(self):
        fixture = HandlerFixture()
        handler = fixture.handler()
        handler.path = "/v1/web-ready"
        handler.headers["X-Leshy-Readiness"] = NONCE
        handler.do_GET()
        expected = hmac.new(SECRET, NONCE.encode("ascii"), hashlib.sha256).hexdigest()
        self.assertEqual(handler.response, HTTPStatus.OK)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"proof": expected})
        self.assertNotIn(NONCE.encode("ascii"), handler.wfile.getvalue())
        self.assertTrue(handler.close_connection)
        self.assertIn(("Cache-Control", "no-store"), handler.sent_headers)
        for values, secret in (([], SECRET), ([NONCE, NONCE], SECRET), (["bad"], SECRET), ([NONCE.upper()], SECRET), ([NONCE], None), ([NONCE], b"short")):
            with self.subTest(values=values, secret_length=len(secret or b"")):
                handler = fixture.handler()
                handler.path = "/v1/web-ready"
                handler.server._leshy_readiness_secret = secret
                for value in values:
                    handler.headers["X-Leshy-Readiness"] = value
                handler.do_GET()
                self.assertEqual(handler.response, HTTPStatus.NOT_FOUND)
                self.assertEqual(handler.wfile.getvalue(), b"")
        other = fixture.handler()
        other.path = "/v1/web-ready"
        other.headers["X-Leshy-Readiness"] = NONCE
        other.server._leshy_readiness_secret = b"t" * 32
        other.do_GET()
        self.assertNotEqual(json.loads(other.wfile.getvalue())["proof"], expected)

    def test_http_probe_validates_mac_framing_size_deadline_cancel_and_numeric_target(self):
        fixture = ProbeFixture()
        self.assertTrue(fixture.probe())
        self.assertEqual(fixture.sock.connected, [("192.0.2.10", 8765)])
        self.assertIn(b"GET /v1/web-ready HTTP/1.1", b"".join(fixture.sock.sent))
        self.assertNotIn(b"Authorization:", b"".join(fixture.sock.sent))
        self.assertTrue(fixture.sock.closed)
        wrong = json.dumps({"proof": "0" * 64}).encode("ascii")
        bad = [ProbeFixture(body=wrong), ProbeFixture(status=500),
               ProbeFixture(headers=[("Content-Type", "application/json"), ("Content-Length", "257")]),
               ProbeFixture(headers=[("Content-Type", "application/json"), ("Content-Length", "1"), ("Content-Length", "1")]),
               ProbeFixture(headers=[("Content-Type", "application/json"), ("Content-Length", "1"), ("Transfer-Encoding", "chunked")]),
               ProbeFixture(headers=[("Content-Type", "application/json"), ("Content-Length", "1"), ("Content-Encoding", "gzip")]),
               ProbeFixture(headers=[("Content-Type", "text/html"), ("Content-Length", "1")]),
               ProbeFixture(chunks=[(11.0, b"HTTP/1.1 200 OK\r\n")])]
        for item in bad:
            self.assertFalse(item.probe())
            self.assertTrue(item.sock.closed)
        cancelled = ProbeFixture()
        self.assertFalse(cancelled.probe(stopped=lambda: True))
        self.assertEqual(cancelled.created, [])
        dns = ProbeFixture()
        self.assertFalse(dns.probe(address=("untrusted.example.invalid", 8765)))
        self.assertEqual(dns.created, [])
        ipv6 = ProbeFixture()
        self.assertTrue(ipv6.probe(family=socket.AF_INET6, address=("2001:db8::10", 8765, 0, 0)))
        self.assertEqual(ipv6.sock.connected, [("2001:db8::10", 8765, 0, 0)])

    def test_tls_probe_requires_verification_and_exact_pin_before_get_and_store_selects_san(self):
        fixture, context = ProbeFixture(), TLSContext()
        self.assertTrue(fixture.probe(tls_context=context, server_hostname=HOST, fingerprint_sha256=PIN))
        self.assertEqual(context.wraps, [HOST])
        self.assertIn(("Host: " + HOST + ":8765").encode("ascii"), b"".join(fixture.sock.sent))
        mismatch = ProbeFixture()
        self.assertFalse(mismatch.probe(tls_context=TLSContext(), server_hostname=HOST, fingerprint_sha256="0" * 64))
        self.assertEqual(mismatch.sock.sent, [])
        for field, value in (("verify_mode", ssl.CERT_NONE), ("check_hostname", False), ("verify_flags", 0)):
            item, context = ProbeFixture(), TLSContext()
            setattr(context, field, value)
            self.assertFalse(item.probe(tls_context=context, server_hostname=HOST, fingerprint_sha256=PIN))
            self.assertEqual(item.created, [])
        invalid_san, context = ProbeFixture(), TLSContext()
        context.failure = ssl.SSLCertVerificationError("Имитация неверного SAN")
        self.assertFalse(invalid_san.probe(tls_context=context, server_hostname=HOST, fingerprint_sha256=PIN))
        self.assertEqual(invalid_san.sock.sent, [])

        class ClientContext:
            def __init__(self, protocol):
                self.protocol, self.verify_flags, self.chains = protocol, 0, []

            def load_verify_locations(self, **values):
                self.chains.append(values)

        fake_ssl = SimpleNamespace(SSLContext=ClientContext, PROTOCOL_TLS_CLIENT=ssl.PROTOCOL_TLS_CLIENT,
                                   TLSVersion=ssl.TLSVersion, VERIFY_X509_PARTIAL_CHAIN=ssl.VERIFY_X509_PARTIAL_CHAIN)
        scope = namespace(ssl=fake_ssl, ipaddress=ipaddress)
        definitions("web_certificates.py", {"_matches_host", "covers_hostname"}, scope)
        cls = selected_class("web_certificates.py", "WebCertificateStore", {"readiness_tls_snapshot"}, scope)
        store = cls.__new__(cls)
        store.lock, store._readiness_context = Lock(), None
        store._public_chain_pem = "synthetic-public-chain"
        store._metadata = {"ip_addresses": ["192.0.2.10"], "dns_names": [HOST], "fingerprint_sha256": PIN}
        store.context = lambda: object()
        selected, pin, name = store.readiness_tls_snapshot("")
        self.assertEqual((pin, name), (PIN, "192.0.2.10"))
        self.assertEqual(selected.chains, [{"cadata": "synthetic-public-chain"}])
        self.assertTrue(selected.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)
        self.assertIs(store.readiness_tls_snapshot(HOST)[0], selected)
        self.assertIsNone(store.readiness_tls_snapshot("other.example.invalid"))
        store._metadata.update(ip_addresses=[], dns_names=["*.example.invalid"])
        self.assertEqual(store.readiness_tls_snapshot("")[2], "readiness.example.invalid")
        store._metadata.update(dns_names=[])
        self.assertIsNone(store.readiness_tls_snapshot(""))

    def test_runtime_notifies_only_after_probe_and_rejects_stop_replacement_or_changed_certificate(self):
        fixture = RuntimeFixture()
        value = fixture.runtime
        value._verify_listeners()
        value._notify_ready()
        self.assertEqual(fixture.calls, [])
        self.assertFalse(value._bindings["http"]["ready"])
        fixture.behavior = lambda **options: True
        value._verify_listeners()
        value._notify_ready()
        value._notify_ready()
        self.assertEqual(fixture.calls, ["http://192.0.2.10:8765/"])
        self.assertLessEqual(fixture.probes[-1]["deadline"], fixture.clock.value + 0.5)

        for change in ("stop", "server", "thread", "failed", "certificate", "hostname", "context"):
            with self.subTest(change=change):
                fixture = RuntimeFixture(tls=change in {"certificate", "hostname", "context"})
                value = fixture.runtime

                def raced(**options):
                    if change == "stop":
                        value._stopping.set()
                    elif change == "server":
                        value._servers[fixture.name] = object()
                    elif change == "thread":
                        fixture.thread.alive = False
                    elif change == "failed":
                        fixture.server._leshy_failed.set()
                    elif change == "certificate":
                        fixture.certificate.pin = "1" * 64
                    elif change == "hostname":
                        value.configuration["hostname"] = "changed.example.invalid"
                    elif change == "context":
                        fixture.certificate.alive = False
                    return True

                fixture.behavior = raced
                value._verify_listeners()
                value._notify_ready()
                self.assertFalse(value._bindings[fixture.name]["ready"])
                self.assertEqual(fixture.calls, [])

        fixture = RuntimeFixture(tls=True)
        value = fixture.runtime
        value._bindings["https"]["ready"] = value._states["https"]["ready"] = True
        prepared = object()
        saved = value.install_prepared_certificate(prepared)
        self.assertEqual(fixture.certificate.installs, [prepared])
        self.assertFalse(saved["active_for_new_connections"])
        self.assertFalse(value._bindings["https"]["ready"])
        value._notify_ready()
        self.assertEqual(fixture.calls, [])
        fixture.behavior = lambda **options: True
        value._verify_listeners()
        value._notify_ready()
        self.assertEqual(fixture.calls, ["https://" + HOST + ":8766/"])
        stopped = RuntimeFixture()
        stopped.runtime._stopping.set()
        stopped.runtime._verify_listeners()
        stopped.runtime._notify_ready()
        self.assertEqual(stopped.probes, [])
        self.assertEqual(stopped.calls, [])


if __name__ == "__main__":
    unittest.main()
