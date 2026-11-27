"""Восемь изолированных проверок подключения локального CA к Web runtime.

Запуск требует отдельного разрешения владельца. Исходники приложения здесь
только разбираются в AST; исполняются выбранные определения с подменёнными
CA, хранилищем, потоками и HTTP-обработчиком. Нет импортов leshy, сети,
создания сертификатов/ключей, реальных потоков или установки доверия ОС.
"""
from __future__ import annotations

import ast
import builtins
import __future__ as future_flags
from contextlib import contextmanager
from email.message import Message
from http import HTTPStatus
import io
import ipaddress
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit


SOURCE = Path(__file__).resolve().parents[1] / "src" / "leshy"
CONFIG = {"hostname": "192.0.2.10", "http_enabled": True, "https_enabled": True,
          "http_port": 8765, "https_port": 8766}
CONSENT = {"names": ["192.0.2.10"], "confirm": True,
           "trust_consent": True, "auto_renew": True}


def denied(*args, **kwargs):
    raise AssertionError("Реальные потоки, сеть и побочные действия запрещены")


def definitions(filename, names, namespace):
    """Ни одной инструкции module-level из приложения, кроме выбранных def/class."""
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8"), filename)
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    found = {node.name for node in nodes}
    if found != set(names):
        raise AssertionError(f"Отсутствуют определения: {set(names) - found}")
    selected = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    code = compile(selected, str(SOURCE / filename), "exec",
                   flags=future_flags.annotations.compiler_flag, dont_inherit=True)
    exec(code, namespace)
    return namespace


class Trace:
    def __init__(self):
        self.events = []
        self.held = []

    def event(self, name):
        self.events.append(name)

    def outside_core(self):
        if "core" in self.held:
            raise AssertionError("CA вызывается при удержании core-lock")

    @contextmanager
    def lock(self, name):
        if name in {"acme", "local"}:
            self.outside_core()
        if name == "acme" and "local" in self.held:
            raise AssertionError("Нарушен порядок ACME → local CA → core")
        self.event(name + "+")
        self.held.append(name)
        try:
            yield
        finally:
            if self.held.pop() != name:
                raise AssertionError("Блокировки освобождены не в обратном порядке")
            self.event(name + "-")


class TraceLock:
    """Повторно используемый fake RLock без настоящей синхронизации/потоков."""
    def __init__(self, trace, name):
        self.trace, self.name = trace, name
        self.contexts = []

    def __enter__(self):
        context = self.trace.lock(self.name)
        self.contexts.append(context)
        return context.__enter__()

    def __exit__(self, *error):
        return self.contexts.pop().__exit__(*error)


class FakeEvent:
    def __init__(self):
        self.value = False

    def is_set(self):
        return self.value

    def set(self):
        self.value = True

    def wait(self, timeout=None):
        return True  # Детерминированный выход serve-loop; ожидания времени нет.


class FakeSettings:
    def __init__(self, brain_dir):
        self.value = dict(CONFIG)

    def effective(self, explicit_port):
        value = self.load()
        if explicit_port is not None:
            value["http_port"] = explicit_port
        return value

    def load(self):
        return dict(self.value)


class FakeCertificates:
    def __init__(self, trace, brain_dir, hostname_provider):
        self.trace = trace
        self.hostname_provider = hostname_provider
        self.prepared = object()
        self.fail_install = False
        self.material = None
        self.fingerprint_reads = 0

    def prepare(self, certificate, key, password=None):
        if "core" not in self.trace.held:
            raise AssertionError("Подготовка должна удерживать core-lock")
        self.trace.event("prepare")
        self.material = (certificate, key, password)
        return self.prepared

    def install_prepared(self, prepared):
        if "core" not in self.trace.held or prepared is not self.prepared:
            raise AssertionError("Установка без core-lock или предварительной подготовки")
        self.trace.event("commit")
        if self.fail_install:
            raise ValueError("synthetic write failure")
        return {"fingerprint_sha256": "ab" * 32, "valid": True}

    def stored_fingerprint(self):
        self.fingerprint_reads += 1
        return "ab" * 32

    def status(self):
        return {"configured": True, "valid": True, "fingerprint_sha256": "ab" * 32}


class FakeRenewalService:
    def __init__(self, trace, name):
        self.trace, self.name = trace, name
        self.enabled = True
        self.in_progress = False
        self.starts = self.stops = 0
        self.ticks = 0
        self.fail_action = False
        self.bodies = []
        self.installed_fingerprint_callback = None

    def replace_certificate_manually(self, installer, *, installed_fingerprint=None):
        with self.trace.lock(self.name):
            self.installed_fingerprint_callback = installed_fingerprint
            before = installed_fingerprint() if installed_fingerprint else None
            old = self.enabled
            self.enabled = False
            self.trace.event(self.name + ":disable")
            try:
                return installer()
            except BaseException:
                if installed_fingerprint is None or installed_fingerprint() == before:
                    self.enabled = old
                    self.trace.event(self.name + ":rollback")
                raise

    @contextmanager
    def configuration_guard(self):
        with self.trace.lock(self.name):
            yield {"enabled": self.enabled, "auto_renew": True, "in_progress": self.in_progress}

    def status(self):
        self.trace.outside_core()
        self.trace.event(self.name + ":status")
        return {"enabled": self.enabled, "browser_trust_verified": False}

    def start(self):
        self.trace.outside_core()
        self.trace.event(self.name + ":start")
        self.starts += 1

    def stop(self):
        self.trace.outside_core()
        self.trace.event(self.name + ":stop")
        self.stops += 1

    def action(self, operation, body):
        self.trace.outside_core()
        if "acme" not in self.trace.held:
            raise AssertionError("Операция без внешней ACME-блокировки")
        if self.name == "acme" and "local" not in self.trace.held:
            raise AssertionError("ACME изменяется без защиты профиля local CA")
        self.trace.event(self.name + ":" + operation)
        self.bodies.append((operation, body))
        if self.fail_action:
            raise ValueError("synthetic action failure")
        self.enabled = True
        return {"operation": operation, "service": self.name}


class RuntimeFixture:
    def __init__(self):
        self.trace = Trace()
        trace = self.trace

        class Local(FakeRenewalService):
            def __init__(self, brain_dir, prepare, install, fingerprint, hostname):
                super().__init__(trace, "local")
                self.arguments = (brain_dir, prepare, install, fingerprint, hostname)

            def issue(self, body):
                return self.action("issue", body)

            def disable(self, body):
                trace.outside_core()
                self.bodies.append(("disable", body))
                self.enabled = False
                return {"enabled": False}

            def tick(self):
                with trace.lock("local"):
                    if "acme" not in trace.held:
                        raise AssertionError("Восстановление до bind требует внешней ACME-блокировки")
                    self.ticks += 1
                    trace.event("local:tick")
                    return {"enabled": self.enabled}

        class Acme(FakeRenewalService):
            def __init__(self, brain_dir, install, http_ready):
                super().__init__(trace, "acme")
                self.installer, self.http_ready = install, http_ready

            def configure(self, body):
                return self.action("configure", body)

            def issue(self, body):
                return self.action("issue", body)

        def imports(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {"web_acme", "leshy.web_acme"}:
                return SimpleNamespace(WebAcmeService=Acme)
            if name in {"web_local_ca", "leshy.web_local_ca"}:
                return SimpleNamespace(WebLocalCAService=Local)
            raise AssertionError(f"Импорт приложения запрещён: {name}")

        safe_builtins = dict(vars(builtins), __import__=imports, open=denied)
        lock_names = iter(("core", "lifecycle"))
        namespace = {"__name__": "leshy._isolated_runtime", "__package__": "leshy",
                     "__builtins__": safe_builtins, "Path": Path,
                     "WebTransportSettings": FakeSettings,
                     "WebCertificateStore": lambda *args: FakeCertificates(trace, *args),
                     "WebLocalCAService": Local, "WebAcmeService": Acme,
                     "os": SimpleNamespace(environ={}),
                     "threading": SimpleNamespace(RLock=lambda: TraceLock(trace, next(lock_names)),
                                                   Event=FakeEvent, Thread=denied),
                     "_listener_callback": SimpleNamespace(get=lambda: None),
                     "covers_hostname": lambda certificate, host: True,
                     "time": SimpleNamespace(monotonic=lambda: 0.0), "probe_listener": denied,
                     "problem": lambda *args, **kwargs: trace.event("diagnostic")}
        runtime_class = definitions("web_transport_runtime.py", {"WebTransportRuntime"}, namespace)["WebTransportRuntime"]
        self.runtime = runtime_class("synthetic-brain", "0.0.0.0")
        trace.events.clear()


class FakeParentHandler:
    def _existing_web_owner(self):
        return self.owner

    def _json(self, status, body):
        self.response, self.json_body = status, body

    def send_response(self, status):
        self.response = status

    def send_header(self, key, value):
        self.response_headers[key.lower()] = value

    def end_headers(self):
        self.headers_ended = True

    def do_GET(self):
        raise AssertionError("Новый GET route не подключён")

    def do_POST(self):
        raise AssertionError("Новый POST route не подключён")


class HandlerFixture:
    class Connection:
        def __init__(self):
            self.timeout = 60

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeout = value

    class TLS(Connection):
        pass

    def __init__(self):
        self.public_calls, self.issue_calls, self.acme_calls, self.disable_calls = [], [], [], []

        def public():
            self.public_calls.append(True)
            return b"synthetic-public-DER"

        def issue(body):
            self.issue_calls.append(body)
            return {"configured": True}

        def configure_acme(body):
            self.acme_calls.append(("configure", body))

        def issue_acme(body):
            self.acme_calls.append(("issue", body))

        def disable(body):
            self.disable_calls.append(body)

        self.service = SimpleNamespace(local_ca=SimpleNamespace(public_certificate=public),
                                       issue_local_certificate=issue, disable_local_ca=disable,
                                       configure_acme=configure_acme, issue_acme=issue_acme,
                                       status=lambda: {})
        self.links = [{"user_id": "12345"}]
        app = SimpleNamespace(web_security=self.service,
                              owner_identity=SimpleNamespace(cookie_name="owner",
                                  status=lambda owner: {"telegram": self.links}))
        telegram = SimpleNamespace(_access=lambda: {"owner_id": "12345"})
        namespace = {"__name__": "leshy._isolated_handler",
                     "__builtins__": dict(vars(builtins), __import__=denied, open=denied),
                     "HTTPStatus": HTTPStatus,
                     "ssl": SimpleNamespace(SSLSocket=self.TLS), "ipaddress": ipaddress,
                     "urlsplit": urlsplit, "re": re, "json": json,
                     "_PREFIX": "/v1/web-security/", "_VOICE_PATHS": set(),
                     "_MAX_JSON": 512 * 1024, "_BODY_DEADLINE_SECONDS": 10.0,
                     "READINESS_PATH": "/v1/web-ready", "response_proof": denied,
                     "time": SimpleNamespace(monotonic=lambda: 0.0),
                     "problem": lambda *args, **kwargs: None,
                     "_PREVIOUS_MAKE_HANDLER": lambda *args: FakeParentHandler}
        factory = definitions("web_server_impl18.py",
                              {"_origin_tuple", "_unambiguous_object", "_SecurityBodyTimeout", "make_handler"}, namespace)["make_handler"]
        self.handler_class = factory(app, telegram, None, None)

    def request(self, method, route, *, tls=False, peer="192.0.2.20", host="192.0.2.10:8765",
                owner="web:owner", ui="network-security", origin=None, forwarded=False):
        handler = self.handler_class.__new__(self.handler_class)
        handler.connection = self.TLS() if tls else self.Connection()
        handler.client_address = (peer, 43210)
        handler.owner = owner
        handler.path = "/v1/web-security/" + route
        headers = Message()
        headers["Host"] = host
        if ui is not None:
            headers["X-Leshy-UI"] = ui
        headers["Origin"] = origin or ("https" if tls else "http") + "://" + host
        if forwarded:
            headers["X-Forwarded-Proto"] = "https"
            headers["X-Forwarded-For"] = "127.0.0.1"
        encoded = json.dumps(CONSENT).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(encoded))
        handler.headers, handler.rfile, handler.wfile = headers, io.BytesIO(encoded), io.BytesIO()
        handler.response, handler.json_body = None, None
        handler.response_headers, handler.headers_ended = {}, False
        getattr(handler, "do_" + method)()
        return handler


class WebLocalCAIntegrationTests(unittest.TestCase):
    def assert_order(self, events, *expected):
        position = 0
        for item in expected:
            position = events.index(item, position) + 1

    def test_constructor_wires_services_without_ca_or_thread_work(self):
        fixture = RuntimeFixture()
        runtime = fixture.runtime
        brain, prepare, install, fingerprint, hostname = runtime.local_ca.arguments
        self.assertEqual(brain, "synthetic-brain")
        self.assertIs(prepare.__self__, runtime)
        self.assertEqual(prepare.__func__.__name__, "prepare_certificate")
        self.assertIs(install.__self__, runtime)
        self.assertEqual(install.__func__.__name__, "install_prepared_certificate")
        self.assertIs(fingerprint.__self__, runtime.certificates)
        self.assertEqual(fingerprint(), "ab" * 32)
        runtime.settings.value["hostname"] = "new-host.example"
        self.assertEqual(hostname(), "new-host.example")
        self.assertIs(runtime.acme.installer.__self__, runtime)
        self.assertEqual(runtime.acme.installer.__func__.__name__, "install_certificate")
        self.assertEqual(runtime.local_ca.starts, 0)
        self.assertEqual(fixture.trace.events, [])

    def test_acme_install_releases_core_before_local_renewal_guard(self):
        fixture = RuntimeFixture()
        runtime, trace = fixture.runtime, fixture.trace
        with trace.lock("acme"):
            result = runtime.acme.installer("certificate", "private-key", "password")
        self.assertEqual(runtime.certificates.material, ("certificate", "private-key", "password"))
        self.assertEqual(result["fingerprint_sha256"], "ab" * 32)
        self.assertFalse(runtime.local_ca.enabled)
        self.assertTrue(runtime.acme.enabled)
        self.assert_order(trace.events, "prepare", "core-", "local+", "commit", "core-", "local-")
        self.assertEqual(trace.held, [])

    def test_manual_import_obeys_order_and_rolls_back_both_profiles_on_failure(self):
        for fail in (False, True):
            with self.subTest(write_failure=fail):
                fixture = RuntimeFixture()
                runtime, trace = fixture.runtime, fixture.trace
                runtime.certificates.fail_install = fail
                if fail:
                    with self.assertRaisesRegex(ValueError, "synthetic write failure"):
                        runtime.import_certificate("certificate", "private-key")
                else:
                    runtime.import_certificate("certificate", "private-key")
                self.assert_order(trace.events, "prepare", "core-", "acme+", "local+", "commit",
                                  "core-", "local-", "acme-")
                self.assertEqual(runtime.acme.enabled, fail)
                self.assertEqual(runtime.local_ca.enabled, fail)
                callback = runtime.acme.installed_fingerprint_callback
                self.assertIs(callback.__self__, runtime.certificates)
                self.assertEqual(callback.__func__.__name__, "stored_fingerprint")
                self.assertGreaterEqual(runtime.certificates.fingerprint_reads, 1 + int(fail))
                if fail:
                    self.assert_order(trace.events, "local:rollback", "acme:rollback")
                self.assertEqual(trace.held, [])

        # Проверяется действительный метод ACME, а не только wiring fake-сервисов:
        # ошибка после смены leaf не возвращает прежнее автоматическое продление.
        tree = ast.parse((SOURCE / "web_acme.py").read_text(encoding="utf-8"))
        service_class = next(node for node in tree.body
                             if isinstance(node, ast.ClassDef) and node.name == "WebAcmeService")
        method = next(node for node in service_class.body
                      if isinstance(node, ast.FunctionDef) and node.name == "replace_certificate_manually")
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        namespace = {"__name__": "leshy._isolated_acme_rollback",
                     "__builtins__": dict(vars(builtins), __import__=denied, open=denied),
                     "_AcmeFailure": RuntimeError}
        exec(compile(module, str(SOURCE / "web_acme.py"), "exec",
                     flags=future_flags.annotations.compiler_flag, dont_inherit=True), namespace)
        replace = namespace["replace_certificate_manually"]
        old_fingerprint, new_fingerprint = "ab" * 32, "cd" * 32
        for outcome, save_failure in (("same", False), ("new", False), ("missing", False),
                                      ("error", False), ("same", True)):
            with self.subTest(actual_acme_rollback=outcome, cleanup_save_failure=save_failure):
                trace, saved, fingerprints = Trace(), [], []
                previous = {"enabled": True, "auto_renew": True, "consent_scope": "synthetic-scope",
                            "state": "issued", "attention_required": False,
                            "outcome_unknown": False, "last_error": ""}
                service = SimpleNamespace(_lock=TraceLock(trace, "acme"), config=dict(previous),
                                          _job=None, _generation=0, _challenges={"token": "synthetic"})
                original = ValueError("synthetic final local profile write failure")

                def fingerprint():
                    fingerprints.append(True)
                    if len(fingerprints) == 1 or outcome == "same":
                        return old_fingerprint
                    if outcome == "error":
                        raise OSError("synthetic fingerprint read failure")
                    return new_fingerprint if outcome == "new" else ""

                def save():
                    saved.append(dict(service.config))
                    if save_failure and len(saved) >= 2:
                        raise OSError("synthetic rollback persistence failure")

                def install():
                    self.assertFalse(service.config["enabled"])
                    self.assertFalse(service.config["auto_renew"])
                    raise original

                service._save = save
                with self.assertRaises(ValueError) as caught:
                    replace(service, install, installed_fingerprint=fingerprint)
                self.assertIs(caught.exception, original)
                self.assertEqual(len(fingerprints), 2)
                self.assertFalse(saved[0]["enabled"])
                self.assertFalse(saved[0]["auto_renew"])
                self.assertEqual(service._challenges, {})
                self.assertEqual(service._generation, 1)
                if outcome == "same" and not save_failure:
                    self.assertEqual(service.config, previous)
                    self.assertEqual(saved[-1], previous)
                else:
                    self.assertFalse(service.config["enabled"])
                    self.assertFalse(service.config["auto_renew"])
                    self.assertEqual(service.config["consent_scope"], "")
                    self.assertEqual(service.config["state"], "outcome_unknown")
                    self.assertTrue(service.config["attention_required"])
                    self.assertTrue(service.config["outcome_unknown"])
                self.assertEqual(trace.held, [])

    def test_local_issue_starts_only_running_runtime_and_stopping_blocks_changes(self):
        busy = RuntimeFixture()
        busy.runtime.acme.in_progress = True
        with self.assertRaisesRegex(ValueError, "завершения текущего запроса ACME"):
            busy.runtime.issue_local_certificate(dict(CONSENT))
        self.assertEqual(busy.runtime.local_ca.bodies, [])
        self.assertTrue(busy.runtime.acme.enabled)
        self.assertEqual(busy.runtime.local_ca.starts, 0)
        for running in (False, True):
            with self.subTest(running=running):
                fixture = RuntimeFixture()
                runtime, trace = fixture.runtime, fixture.trace
                runtime._running = running
                result = runtime.issue_local_certificate(dict(CONSENT))
                self.assertEqual(result, {"operation": "issue", "service": "local"})
                self.assertEqual(runtime.local_ca.bodies, [("issue", CONSENT)])
                self.assertFalse(runtime.acme.enabled)
                self.assertIs(runtime.acme.installed_fingerprint_callback.__self__, runtime.certificates)
                self.assertGreaterEqual(runtime.certificates.fingerprint_reads, 1)
                self.assertEqual(runtime.local_ca.starts, int(running))
                self.assert_order(trace.events, "acme+", "local:issue", "acme-")
                runtime.disable_local_ca({"confirm": True})
                self.assertEqual(runtime.local_ca.bodies[-1], ("disable", {"confirm": True}))
                self.assertFalse(runtime.local_ca.enabled)
        fixture = RuntimeFixture()
        fixture.runtime._stopping.set()
        with self.assertRaises(RuntimeError):
            fixture.runtime.issue_local_certificate(dict(CONSENT))
        self.assertEqual(fixture.runtime.local_ca.bodies, [])
        self.assertEqual(fixture.runtime.local_ca.starts, 0)
        self.assertTrue(fixture.runtime.acme.enabled)

        # Явная ошибка до смены leaf восстанавливает прежний профиль ACME.
        for running in (False, True):
            with self.subTest(issue_failure_running=running):
                fixture = RuntimeFixture()
                fixture.runtime._running = running
                fixture.runtime.local_ca.fail_action = True
                with self.assertRaisesRegex(ValueError, "synthetic action failure"):
                    fixture.runtime.issue_local_certificate(dict(CONSENT))
                self.assertTrue(fixture.runtime.acme.enabled)
                self.assertEqual(fixture.runtime.certificates.fingerprint_reads, 2)
                self.assertEqual(fixture.runtime.local_ca.starts, 0)

    def test_acme_actions_guard_local_profile_and_restore_it_on_error(self):
        for operation in ("configure", "issue"):
            for fail in (False, True):
                with self.subTest(operation=operation, action_failure=fail):
                    fixture = RuntimeFixture()
                    runtime, trace = fixture.runtime, fixture.trace
                    runtime.acme.fail_action = fail
                    action = getattr(runtime, operation + "_acme")
                    body = {"confirm": True}
                    if fail:
                        with self.assertRaisesRegex(ValueError, "synthetic action failure"):
                            action(body)
                    else:
                        self.assertEqual(action(body), {"operation": operation, "service": "acme"})
                    self.assert_order(trace.events, "acme+", "local+", "acme:" + operation,
                                      "local-", "acme-")
                    self.assertEqual(runtime.local_ca.enabled, fail)
                    self.assertEqual(runtime.acme.bodies, [(operation, body)])
                    self.assertEqual(trace.held, [])
        fixture = RuntimeFixture()
        runtime, trace = fixture.runtime, fixture.trace

        def saved_error(body):
            trace.outside_core()
            self.assertIn("acme", trace.held)
            self.assertIn("local", trace.held)
            return {"state": "error", "last_error": "synthetic discovery failure"}

        runtime.acme.configure = saved_error
        self.assertEqual(runtime.configure_acme({"confirm": True})["state"], "error")
        # Сохранённое состояние ошибки не бросает исключение: local renewal остаётся выключен.
        self.assertFalse(runtime.local_ca.enabled)
        self.assertNotIn("local:rollback", trace.events)

    def test_status_and_service_lifecycle_run_outside_core_lock(self):
        fixture = RuntimeFixture()
        runtime, trace = fixture.runtime, fixture.trace
        value = runtime.status()
        self.assertTrue(value["capabilities"]["local_ca"])
        self.assertFalse(value["local_ca"]["browser_trust_verified"])
        self.assert_order(trace.events, "core-", "acme:status", "local:status")

        class Thread:
            alive = True

            def is_alive(self):
                return self.alive

            def join(self, timeout):
                trace.outside_core()
                self.alive = False

        runtime._threads["http"] = Thread()
        runtime._serve_forever()
        self.assertTrue(runtime._running)
        # Несовместимый активный ACME-профиль не запускает второй issuer.
        self.assertEqual((runtime.acme.starts, runtime.local_ca.starts), (1, 0))
        runtime.acme.enabled = False
        runtime._start_issuers(local_only=True)
        self.assertEqual((runtime.acme.starts, runtime.local_ca.starts), (1, 1))
        runtime.close()
        self.assertEqual((runtime.acme.stops, runtime.local_ca.stops), (1, 1))
        self.assertFalse(runtime._running)
        self.assertTrue(runtime._stopping.is_set())
        runtime._start_issuers()
        runtime._start_issuers(local_only=True)
        self.assertEqual((runtime.acme.starts, runtime.local_ca.starts), (1, 1))
        self.assertEqual(trace.held, [])

        # Просроченный собственный leaf может восстановиться до HTTPS-only bind;
        # активный/занятый ACME и сигнал остановки запрещают такое восстановление.
        for active, in_progress, stopping in ((False, False, False), (True, False, False),
                                              (False, True, False), (False, False, True)):
            with self.subTest(acme_active=active, acme_busy=in_progress, stopping=stopping):
                before_bind = RuntimeFixture()
                runtime, trace = before_bind.runtime, before_bind.trace
                runtime.acme.enabled, runtime.acme.in_progress = active, in_progress
                if stopping:
                    runtime._stopping.set()

                def bind(protocol):
                    if "core" not in trace.held:
                        raise AssertionError("Привязка требует core-lock")
                    trace.event("bind:" + protocol)
                    runtime._servers[protocol] = SimpleNamespace(server_close=denied)

                runtime._bind = bind
                runtime.bind(object())
                should_tick = not (active or in_progress or stopping)
                self.assertEqual(runtime.local_ca.ticks, int(should_tick))
                if should_tick:
                    self.assert_order(trace.events, "acme+", "local:tick", "acme-", "core+", "bind:http")
                self.assertEqual((runtime.acme.starts, runtime.local_ca.starts), (0, 0))
                self.assertEqual(trace.held, [])

    def test_root_and_issue_routes_require_owner_and_private_transport(self):
        cases = [
            ({}, HTTPStatus.FORBIDDEN),
            ({"forwarded": True}, HTTPStatus.FORBIDDEN),
            ({"tls": True, "host": "192.0.2.10:8766"}, HTTPStatus.OK),
            ({"peer": "127.0.0.1", "host": "127.0.0.1:8765"}, HTTPStatus.OK),
            ({"peer": "::1", "host": "[::1]:8765"}, HTTPStatus.OK),
            ({"peer": "127.0.0.1"}, HTTPStatus.FORBIDDEN),
            ({"tls": True, "owner": None}, HTTPStatus.UNAUTHORIZED),
            ({"tls": True, "owner": "web:local"}, HTTPStatus.UNAUTHORIZED),
            ({"tls": True, "ui": None}, HTTPStatus.FORBIDDEN),
        ]
        for kwargs, expected in cases:
            for method, route in (("GET", "local-ca/root.cer"), ("POST", "local-ca/issue")):
                with self.subTest(method=method, **kwargs):
                    fixture = HandlerFixture()
                    handler = fixture.request(method, route, **kwargs)
                    self.assertEqual(handler.response, expected)
                    self.assertEqual(bool(fixture.public_calls), expected == HTTPStatus.OK and method == "GET")
                    self.assertEqual(bool(fixture.issue_calls), expected == HTTPStatus.OK and method == "POST")
                    if expected == HTTPStatus.OK and method == "GET":
                        self.assertEqual(handler.wfile.getvalue(), b"synthetic-public-DER")
                        self.assertEqual(handler.response_headers["content-type"], "application/x-x509-ca-cert")
                        self.assertEqual(handler.response_headers["cache-control"], "no-store")
                        self.assertIn("attachment", handler.response_headers["content-disposition"])
                        self.assertIn("Leshy-local-root.cer", handler.response_headers["content-disposition"])
                    if expected == HTTPStatus.OK and method == "POST":
                        self.assertEqual(fixture.issue_calls, [CONSENT])
        fixture = HandlerFixture()
        handler = fixture.request("POST", "local-ca/issue", tls=True, origin="https://other.example")
        self.assertEqual(handler.response, HTTPStatus.FORBIDDEN)
        self.assertEqual(fixture.issue_calls, [])
        fixture.links = [{"user_id": "99999"}]
        handler = fixture.request("GET", "local-ca/root.cer", tls=True)
        self.assertEqual(handler.response, HTTPStatus.FORBIDDEN)
        self.assertEqual(fixture.public_calls, [])
        for tls in (False, True):
            fixture = HandlerFixture()
            handler = fixture.request("GET", "status", tls=tls)
            self.assertEqual(handler.response, HTTPStatus.OK)
            self.assertEqual(handler.json_body["connection"]["can_issue_local_ca"], tls)
            self.assertEqual(handler.json_body["connection"]["can_download_local_ca"], tls)
        for operation in ("configure", "issue"):
            fixture = HandlerFixture()
            handler = fixture.request("POST", "acme/" + operation, tls=True)
            self.assertEqual(handler.response, HTTPStatus.OK)
            self.assertEqual(fixture.acme_calls, [(operation, CONSENT)])
        fixture = HandlerFixture()
        handler = fixture.request("POST", "local-ca/disable", tls=True, origin="https://other.example")
        self.assertEqual(handler.response, HTTPStatus.FORBIDDEN)
        self.assertEqual(fixture.disable_calls, [])

    def test_stored_fingerprint_reads_public_disk_leaf_despite_expired_cached_context(self):
        # Только публичный fingerprint: ни сертификат, ни поле private_key_pem не возвращаются.
        trace = Trace()
        calls = []
        digest = bytes(range(32))
        algorithm = object()

        def fingerprint(value):
            self.assertIs(value, algorithm)
            calls.append("fingerprint")
            return digest

        leaf = SimpleNamespace(fingerprint=fingerprint)

        def parse(value):
            calls.append("parse")
            self.assertIn(b"PUBLIC CERTIFICATE", value)
            return [leaf, SimpleNamespace(fingerprint=denied)]

        material = {"certificate_pem": "PUBLIC CERTIFICATE", "private_key_pem": "PRIVATE-NEVER-RETURN"}

        class FakePath:
            size = 100
            exists_value = True

            def is_file(self):
                return self.exists_value

            def stat(self):
                return SimpleNamespace(st_size=self.size)

        path, root = FakePath(), object()

        def reject_link(value):
            self.assertIn(value, (root, path))
            calls.append("reject-root" if value is root else "reject-link")

        namespace = {"__name__": "leshy._isolated_certificates",
                     "__builtins__": dict(vars(builtins), __import__=denied, open=denied), "json": json,
                     "reject_link": reject_link, "load_secret": lambda path: json.dumps(material),
                     "x509": SimpleNamespace(load_pem_x509_certificates=parse),
                     "hashes": SimpleNamespace(SHA256=lambda: algorithm),
                     "inspect_certificate": denied}
        tree = ast.parse((SOURCE / "web_certificates.py").read_text(encoding="utf-8"))
        store = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WebCertificateStore")
        method = next(node for node in store.body if isinstance(node, ast.FunctionDef) and node.name == "stored_fingerprint")
        selected = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        code = compile(selected, str(SOURCE / "web_certificates.py"), "exec",
                       flags=future_flags.annotations.compiler_flag, dont_inherit=True)
        exec(code, namespace)
        instance = SimpleNamespace(root=root, path=path, lock=TraceLock(trace, "core"), _context=object(),
                                   _metadata={"valid": False, "valid_until": "2000-01-01T00:00:00+00:00",
                                              "fingerprint_sha256": "stale"})
        actual = namespace["stored_fingerprint"](instance)
        self.assertEqual(actual, digest.hex())
        self.assertNotIn("PRIVATE", actual)
        self.assert_order(calls, "reject-root", "reject-link", "parse", "fingerprint")
        for failure in ("malformed", "oversized", "link"):
            with self.subTest(failure=failure):
                instance.lock = TraceLock(trace, "core")
                path.size = 2 * 1024 * 1024 if failure == "oversized" else 100
                namespace["load_secret"] = lambda path: "{invalid"
                if failure == "link":
                    namespace["reject_link"] = lambda path: (_ for _ in ()).throw(ValueError("symlink"))
                try:
                    result = namespace["stored_fingerprint"](instance)
                except ValueError:
                    self.assertEqual(failure, "link")
                else:
                    self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
