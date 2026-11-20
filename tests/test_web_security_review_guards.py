"""Четыре изолированные проверки замечаний независимого Web/TLS-ревью.

Запуск требует отдельного разрешения владельца. Определения приложения
извлекаются из AST без импорта leshy. Используются только синтетические
токены, TEST-NET адреса, подменённые часы, сокеты и память вместо сети.
Сервер, CA, ключи, потоки и установка доверия ОС не запускаются.
"""
from __future__ import annotations

import ast
import builtins
import __future__ as future_flags
from http import HTTPStatus
import http.client
import hmac
import io
import ipaddress
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from urllib import error
from urllib.parse import urlsplit


SOURCE = Path(__file__).resolve().parents[1] / "src" / "leshy"
RESOURCE = "https://ca.example.invalid/acme/resource"
TOKEN = "synthetic-test-token"


def denied(*args, **kwargs):
    raise AssertionError("Импорты приложения, файлы и реальная сеть запрещены")


def definitions(filename, names, namespace):
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8"), filename)
    selected = [node for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError("Изменился контракт извлекаемых определений")
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    code = compile(module, str(SOURCE / filename), "exec",
                   flags=future_flags.annotations.compiler_flag, dont_inherit=True)
    exec(code, namespace)
    return namespace


def safe_namespace(name):
    return {"__name__": name,
            "__builtins__": dict(vars(builtins), __import__=denied, open=denied)}


class VoiceFixture:
    class TLS:
        pass

    def __init__(self, token=""):
        class Parent:
            def _cookie_value(self, name):
                self.cookie_reads.append(name)
                return self.cookies.get(name, "")

            def _existing_web_owner(self):
                return self.owner if self._cookie_value("owner") else None

            def _json(self, status, body):
                self.response, self.json_body = status, body

        namespace = safe_namespace("leshy._isolated_voice_review")
        namespace.update(HTTPStatus=HTTPStatus, hmac=hmac, ipaddress=ipaddress,
                         urlsplit=urlsplit, re=re,
                         os=SimpleNamespace(getenv=lambda key, default="": token if key == "LESHY_API_TOKEN" else default),
                         ssl=SimpleNamespace(SSLSocket=self.TLS),
                         _PREVIOUS_MAKE_HANDLER=lambda *args: Parent)
        app = SimpleNamespace(web_security=object(),
                              owner_identity=SimpleNamespace(cookie_name="owner"))
        factory = definitions("web_server_impl18.py", {"_origin_tuple", "make_handler"}, namespace)["make_handler"]
        self.handler_class = factory(app, None, None, None)

    def handler(self, *, tls=False, peer="192.0.2.20", host="192.0.2.10:8765",
                origin=None, ui="voice", bearer=None, owner="web:synthetic", cookies=None,
                forwarded=False):
        handler = self.handler_class.__new__(self.handler_class)
        handler.connection = self.TLS() if tls else object()
        handler.client_address = (peer, 54321)
        handler.owner, handler.cookies = owner, dict(cookies or {})
        handler.cookie_reads, handler.response, handler.json_body = [], None, None
        handler.headers = {"Host": host, "Origin": origin or ("https" if tls else "http") + "://" + host}
        if ui is not None:
            handler.headers["X-Leshy-UI"] = ui
        if bearer is not None:
            handler.headers["Authorization"] = "Bearer " + bearer
        if forwarded:
            handler.headers.update({"X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"})
        return handler


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def monotonic(self):
        return self.value


class Guard:
    def __init__(self, failure=None, fail_at=None):
        self.calls, self.failure, self.fail_at = 0, failure, fail_at

    def __call__(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise self.failure


class FakeSocket:
    def __init__(self, clock, steps=()):
        self.clock, self.steps = clock, list(steps)
        self.timeouts, self.reads, self.file_calls = [], 0, []
        self.raw = None

    def settimeout(self, value):
        if self.raw is not None and self.raw.closed:
            raise OSError("Учебный сокет закрыт после EOF")
        self.timeouts.append(value)

    def next_part(self):
        self.reads += 1
        elapsed, value = self.steps.pop(0) if self.steps else (0.0, b"")
        self.clock.value += elapsed
        if isinstance(value, BaseException):
            raise value
        return value

    def makefile(self, mode, *, buffering):
        self.file_calls.append((mode, buffering))
        sock = self

        class Raw(io.RawIOBase):
            def readable(self):
                return True

            def readinto(self, target):
                part = sock.next_part()
                if len(part) > len(target):
                    raise AssertionError("Учебный фрагмент больше буфера")
                target[:len(part)] = part
                return len(part)

        self.raw = Raw()
        return self.raw


class FakeBaseResponse:
    def __init__(self, sock, *args, **kwargs):
        self.proxy = sock
        self.sock = getattr(sock, "sock", sock)
        self.fp, self._method = object(), "GET"
        self.headers = {"Replay-Nonce": "synthetic_nonce_123", "Content-Type": "application/json"}
        self.code, self.closes, self.close_failure = 200, 0, None

    def read1(self, amount=-1):
        part = self.sock.next_part()
        if amount >= 0 and len(part) > amount:
            raise AssertionError("Учебный фрагмент нарушает предел read1")
        return part

    def getcode(self):
        return self.code

    def close(self):
        self.closes += 1
        self.fp = None
        if self.close_failure:
            raise self.close_failure


class FakeHTTPSHandler:
    def __init__(self, *, context):
        self._context = context

    def do_open(self, connection, req, *, context):
        self.request, self.context = req, context
        return connection("ca.example.invalid", timeout=3.0)


class WireFixture:
    def __init__(self, *, real_http_base=False):
        self.clock = Clock()
        self.contexts = []

        def context():
            value = object()
            self.contexts.append(value)
            return value

        def connection(host, **kwargs):
            return SimpleNamespace(host=host, kwargs=kwargs)

        def request(url, **kwargs):
            return SimpleNamespace(full_url=url, **kwargs)

        self.namespace = safe_namespace("leshy._isolated_acme_review")
        self.namespace.update(io=io, time=self.clock, error=error, re=re, urlsplit=urlsplit,
                              _MAX_RESPONSE=256 * 1024, _NONCE=re.compile(r"[A-Za-z0-9_-]{1,1024}\Z"),
                              http=SimpleNamespace(client=SimpleNamespace(
                                  HTTPResponse=http.client.HTTPResponse if real_http_base else FakeBaseResponse,
                                  HTTPSConnection=connection, HTTPException=http.client.HTTPException)),
                              request=SimpleNamespace(HTTPSHandler=FakeHTTPSHandler, Request=request),
                              ssl=SimpleNamespace(create_default_context=context))
        definitions("web_acme.py", {"_AcmeFailure", "_UnknownOutcome", "_Cancelled",
                                    "_DeadlineReader", "_DeadlineSocket", "_DeadlineHTTPResponse",
                                    "_DeadlineHTTPSHandler", "_AcmeWire"}, self.namespace)

    def wire(self, response=None, *, open_failure=None, guard=None, deadline=20.0):
        namespace = self.namespace
        wire = namespace["_AcmeWire"].__new__(namespace["_AcmeWire"])
        wire.url, wire.origin = RESOURCE, urlsplit(RESOURCE).hostname
        wire.guard, wire.deadline, wire.nonce = guard or Guard(), deadline, ""
        calls = []

        def open_response(req, *, timeout):
            calls.append((req, timeout))
            if open_failure is not None:
                raise open_failure
            return response

        wire.opener = SimpleNamespace(open=open_response)
        return wire, calls


class WebSecurityReviewGuardTests(unittest.TestCase):
    def test_bearer_rejects_remote_http_and_forwarded_claims(self):
        fixture = VoiceFixture(TOKEN)
        cases = [
            ({}, False),
            ({"forwarded": True}, False),
            ({"peer": "192.0.2.20", "host": "localhost:8765"}, False),
            ({"peer": "127.0.0.1"}, False),
            ({"tls": True, "host": "192.0.2.10:8766"}, True),
            ({"peer": "127.0.0.1", "host": "localhost:8765"}, True),
            ({"peer": "127.0.0.2", "host": "127.0.0.2:8765"}, True),
            ({"peer": "::1", "host": "[::1]:8765"}, True),
        ]
        for options, allowed in cases:
            with self.subTest(**options):
                handler = fixture.handler(bearer=TOKEN, ui=None, **options)
                self.assertEqual(handler._voice_allowed(), allowed)
                self.assertEqual(handler.response, None if allowed else HTTPStatus.FORBIDDEN)
        handler = fixture.handler(tls=True, bearer="wrong-synthetic-token", cookies={"__Host-owner": "cookie"})
        self.assertFalse(handler._voice_allowed())
        self.assertEqual(handler.response, HTTPStatus.UNAUTHORIZED)

    def test_cookie_protocol_isolation_and_voice_origin_ui_token_gate(self):
        fixture = VoiceFixture()
        cookies = {"owner": "plain-session", "__Host-owner": "secure-session", "other": "separate"}
        for tls, expected in ((False, "plain-session"), (True, "secure-session")):
            with self.subTest(tls=tls):
                handler = fixture.handler(tls=tls, cookies=cookies)
                self.assertEqual(handler._cookie_value("owner"), expected)
                self.assertEqual(handler._cookie_value("other"), "separate")
                header = handler._identity_cookie_header("synthetic-cookie")
                self.assertTrue(header.startswith(("__Host-" if tls else "") + "owner="))
                for flag in ("Path=/", "HttpOnly", "SameSite=Strict"):
                    self.assertIn(flag, header)
                self.assertEqual("; Secure" in header, tls)
                self.assertNotIn("Domain=", header)
                self.assertEqual(handler._identity_cookie_header(None), "")
        self.assertFalse(fixture.handler(tls=True, cookies={"owner": "plain-only"})._voice_allowed())
        self.assertFalse(fixture.handler(cookies={"__Host-owner": "secure-only"})._voice_allowed())
        for options in ({}, {"forwarded": True}, {"host": "localhost:8765"}):
            self.assertFalse(fixture.handler(cookies={"owner": "session"}, **options)._voice_allowed())
        for tls, key in ((False, "owner"), (True, "__Host-owner")):
            local = {} if tls else {"peer": "127.0.0.1", "host": "localhost:8765"}
            self.assertTrue(fixture.handler(tls=tls, cookies={key: "session"}, **local)._voice_allowed())
            for options in ({"ui": None}, {"ui": "network-security"},
                            {"origin": "https://other.example.invalid"},
                            {"origin": ("https" if tls else "http") + "://192.0.2.10:9999"},
                            {"origin": ("http" if tls else "https") + "://192.0.2.10:8765"},
                            {"owner": None}):
                with self.subTest(tls=tls, **options):
                    self.assertFalse(fixture.handler(tls=tls, cookies={key: "session"}, **options)._voice_allowed())
        # Настроенный token не допускает переход на cookie-авторизацию.
        handler = VoiceFixture(TOKEN).handler(tls=True, cookies={"__Host-owner": "session"})
        self.assertFalse(handler._voice_allowed())
        self.assertEqual(handler.response, HTTPStatus.UNAUTHORIZED)

    def test_deadline_read1_framing_budget_and_cancellation(self):
        fixture = WireFixture()
        namespace, clock = fixture.namespace, fixture.clock
        response_class = namespace["_DeadlineHTTPResponse"]
        sock = FakeSocket(clock, [(4.0, b"a"), (5.0, b"b"), (0.0, b"")])
        guard = Guard()
        response = response_class(sock, deadline=12.0, guard=guard)
        self.assertEqual([response.read1(2), response.read1(2), response.read1(2)], [b"a", b"b", b""])
        self.assertEqual(sock.timeouts, [10.0, 8.0, 3.0])
        self.assertEqual(guard.calls, 6)
        for phase in ("before", "after"):
            with self.subTest(timeout=phase):
                clock.value = 12.0 if phase == "before" else 11.0
                sock = FakeSocket(clock, [(1.0, b"late")])
                response = response_class(sock, deadline=12.0, guard=Guard())
                with self.assertRaises(TimeoutError):
                    response.read1(4)
                self.assertEqual(sock.reads, int(phase == "after"))
        for fail_at in (1, 2):
            clock.value = 0.0
            sock = FakeSocket(clock, [(0.0, b"discard")])
            guard = Guard(namespace["_Cancelled"]("synthetic cancellation"), fail_at)
            response = response_class(sock, deadline=12.0, guard=guard)
            with self.assertRaises(namespace["_Cancelled"]):
                response.read1(8)
            self.assertEqual(sock.reads, fail_at - 1)
        handler = namespace["_DeadlineHTTPSHandler"](12.0, guard)
        connection = handler.https_open("synthetic-request")
        self.assertEqual(connection.host, "ca.example.invalid")
        self.assertIs(handler.context, fixture.contexts[-1])
        wrapped = connection.response_class(FakeSocket(clock))
        self.assertIs(wrapped._acme_guard, guard)
        self.assertEqual(wrapped._acme_deadline, 12.0)

        # Content-Length закрывает HTTPResponse.fp сразу после последнего байта.
        # Дополнительный raw-loop read1 не должен касаться уже закрытого сокета.
        eof = WireFixture(real_http_base=True)
        sock = FakeSocket(eof.clock, [(0.0, b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nbody")])
        response = eof.namespace["_DeadlineHTTPResponse"](sock, method="GET", deadline=12.0, guard=Guard())
        try:
            response.begin()
            self.assertEqual(response.read1(4), b"body")
            self.assertIsNone(response.fp)
            self.assertTrue(sock.raw.closed)
            timeout_count = len(sock.timeouts)
            self.assertEqual(response.read1(4), b"")
            self.assertEqual(len(sock.timeouts), timeout_count)
        finally:
            response.close()

        # Настоящий stdlib HTTP parser читает только fake RawIOBase: каждый
        # однобайтовый refill расходует общий бюджет, включая header/chunk line.
        for phase in ("headers", "chunk-size"):
            with self.subTest(slow_trickle=phase):
                framing = WireFixture(real_http_base=True)
                header = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                steps = [(1.0, bytes([value])) for value in b"HTTP/1.1 200 OK\r\n"] if phase == "headers" else [
                    (0.0, header), (1.0, b"1"), (1.0, b";"), (1.0, b"a"), (1.0, b"\r\n")]
                sock = FakeSocket(framing.clock, steps)
                response = framing.namespace["_DeadlineHTTPResponse"](sock, method="GET", deadline=2.5, guard=Guard())
                try:
                    with self.assertRaises(TimeoutError):
                        response.begin()
                        response.read1(10)
                finally:
                    response.close()
                self.assertEqual(sock.file_calls, [("rb", 0)])
                self.assertTrue(sock.raw.closed)
                self.assertEqual(framing.clock.value, 3.0)
                self.assertLessEqual(sock.timeouts[-1], 0.5)

    def test_raw_classifies_unknown_post_and_preserves_errors_during_close(self):
        for method in ("GET", "POST"):
            for failure in (TimeoutError("body timeout"), http.client.HTTPException("body framing")):
                with self.subTest(method=method, failure=type(failure).__name__):
                    fixture = WireFixture()
                    response = FakeBaseResponse(FakeSocket(fixture.clock, [(0.0, failure)]))
                    response.close_failure = OSError("synthetic close failure")
                    wire, calls = fixture.wire(response)
                    expected = fixture.namespace["_UnknownOutcome" if method == "POST" else "_AcmeFailure"]
                    with self.assertRaises(expected) as caught:
                        wire.raw(method, RESOURCE, b"synthetic-jws" if method == "POST" else None)
                    self.assertIs(type(caught.exception), expected)
                    self.assertIs(caught.exception.__cause__, failure)
                    self.assertEqual(response.closes, 1)
                    self.assertEqual(len(calls), 1)  # Неоднозначный POST не повторяется.
        for method in ("GET", "POST"):
            fixture = WireFixture()
            failure = http.client.HTTPException("open/header failure")
            wire, calls = fixture.wire(open_failure=failure)
            expected = fixture.namespace["_UnknownOutcome" if method == "POST" else "_AcmeFailure"]
            with self.assertRaises(expected):
                wire.raw(method, RESOURCE)
            self.assertEqual(len(calls), 1)
        fixture = WireFixture()
        response = FakeBaseResponse(FakeSocket(fixture.clock, [(0.0, b'{"ok":true}'), (0.0, b"")]))
        response.close_failure = http.client.HTTPException("cleanup after complete body")
        wire, _ = fixture.wire(response)
        code, headers, body = wire.raw("GET", RESOURCE)
        self.assertEqual((code, body), (200, b'{"ok":true}'))
        self.assertEqual(wire.nonce, headers["replay-nonce"])
        self.assertEqual(response.closes, 1)

        fixture = WireFixture()
        response = FakeBaseResponse(FakeSocket(fixture.clock, [(0.0, AssertionError("HEAD не читает тело"))]))
        wire, _ = fixture.wire(response)
        self.assertEqual(wire.raw("HEAD", RESOURCE)[2], b"")
        self.assertEqual(response.sock.reads, 0)
        self.assertEqual(response.closes, 1)
        fixture.clock.value = 20.0
        wire, calls = fixture.wire(response)
        with self.assertRaises(fixture.namespace["_AcmeFailure"]) as caught:
            wire.raw("POST", RESOURCE)
        self.assertIs(type(caught.exception), fixture.namespace["_AcmeFailure"])
        self.assertEqual(calls, [])  # Бюджет истёк до отправки: неизвестного выпуска нет.

        for fail_body in (False, True):
            fixture = WireFixture()
            part = TimeoutError("HTTPError body timeout") if fail_body else b'{"type":"synthetic-problem"}'
            response = FakeBaseResponse(FakeSocket(fixture.clock, [(0.0, part), (0.0, b"")]))
            http_error = error.HTTPError(RESOURCE, 400, "synthetic", response.headers, response)
            wire, _ = fixture.wire(open_failure=http_error)
            if fail_body:
                with self.assertRaises(fixture.namespace["_UnknownOutcome"]):
                    wire.raw("POST", RESOURCE)
            else:
                code, _, body = wire.raw("POST", RESOURCE)
                self.assertEqual((code, body), (400, part))
            self.assertEqual(response.closes, 1)

        fixture = WireFixture()
        response = FakeBaseResponse(FakeSocket(fixture.clock))
        response.close_failure = OSError("close must not mask cancellation")
        guard = Guard(fixture.namespace["_Cancelled"]("cancel after open"), fail_at=2)
        wire, _ = fixture.wire(response, guard=guard)
        with self.assertRaises(fixture.namespace["_Cancelled"]):
            wire.raw("POST", RESOURCE)
        self.assertEqual(response.closes, 1)
        self.assertEqual(response.sock.reads, 0)


if __name__ == "__main__":
    unittest.main()
