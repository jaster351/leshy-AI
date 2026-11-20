"""Четыре изолированные проверки альтернативного packaging launcher.

Не запускать без отдельного разрешения владельца. Из текущего исходника
исполняется только main, полученный через AST. Аргументы, окружение, пути,
таймеры, блокировка, браузер, publisher и serve подменены. Настоящие модули
Лешего, потоки, сокеты, CA и пользовательские файлы недоступны.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "packaging" / "launcher.py"


class FakePath:
    """Представление пути без обращения к файловой системе."""

    def __init__(self, value):
        self.value = value

    def __truediv__(self, part):
        return FakePath(self.value + "/" + part)

    def resolve(self):
        return self

    def __str__(self):
        return self.value


class FakeLock:
    """Проверить отсутствие повторного входа, не создавая системную блокировку."""

    def __init__(self):
        self.locked = False

    def __enter__(self):
        if self.locked:
            raise AssertionError("Повторный вход в одну блокировку запрещён")
        self.locked = True
        return self

    def __exit__(self, *_args):
        self.locked = False


class FakeTimer:
    """Сохранить callback; вручную допускается его поздний вызов после cancel."""

    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        return self.callback()


class LauncherHarness:
    """Исполнять реальный main с закрытым списком разрешённых импортов."""

    def __init__(self, *, platform="posix", argv=(), environment=None):
        self.environment = dict(environment or {})
        self.argv = list(argv)
        self.platform = platform
        self.timers = []
        self.imports = []
        self.serve_calls = []
        self.publishers = []
        self.browsers = []
        self.browser_open = Mock(return_value=True)
        self.browser_open_error = None
        self.behavior = lambda _host, _port, _callback: None

    def argument_parser(self, *args, **kwargs):
        parser = argparse.ArgumentParser(*args, **kwargs)
        parse = parser.parse_args
        parser.parse_args = lambda: parse(self.argv)
        return parser

    def timer(self, delay, callback):
        value = FakeTimer(delay, callback)
        self.timers.append(value)
        return value

    def publisher(self, brain_dir):
        value = SimpleNamespace(brain_dir=str(brain_dir), start=Mock(), stop=Mock())
        self.publishers.append(value)
        return value

    def browser(self, target):
        value = SimpleNamespace(target=target, open=Mock(return_value=True), close=Mock())
        value.open.side_effect = self.browser_open_error
        self.browsers.append(value)
        return value

    def serve(self, host, port, *, on_listening):
        self.serve_calls.append((host, port, on_listening))
        return self.behavior(host, port, on_listening)

    def import_only_fake(self, name, _globals=None, _locals=None, fromlist=(), level=0):
        modules = {
            "leshy.diagnostic_publisher": ("DiagnosticPublisher", self.publisher),
            "leshy.web_server": ("serve", self.serve),
            "leshy.managed_browser": ("ManagedBrowser", self.browser),
        }
        if level or name not in modules or tuple(fromlist) != (modules[name][0],):
            raise AssertionError("Настоящие импорты приложения запрещены: " + name)
        self.imports.append(name)
        attribute, value = modules[name]
        return SimpleNamespace(**{attribute: value})

    def run(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"), filename=str(SOURCE))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        namespace = {
            "__builtins__": {"__import__": self.import_only_fake, "str": str, "bool": bool, "int": int},
            "argparse": SimpleNamespace(ArgumentParser=self.argument_parser),
            "os": SimpleNamespace(name=self.platform, environ=self.environment,
                                  getenv=lambda key, default=None: self.environment.get(key, default)),
            "threading": SimpleNamespace(Lock=FakeLock, Timer=self.timer),
            "webbrowser": SimpleNamespace(open=self.browser_open),
            "_install_root": lambda: FakePath("isolated-install"),
        }
        exec(compile(ast.Module(body=[main], type_ignores=[]), str(SOURCE), "exec"), namespace)
        return namespace["main"]()


class PackagingWebLauncherTests(unittest.TestCase):
    def assert_publisher_cleaned(self, harness):
        self.assertEqual(len(harness.publishers), 1)
        harness.publishers[0].start.assert_called_once_with()
        harness.publishers[0].stop.assert_called_once_with()
        self.assertEqual(harness.publishers[0].brain_dir, "isolated-install/LeshyBrain")

    def test_default_auto_waits_for_ready_and_uses_actual_https_or_ipv6_url(self):
        for target in ("https://leshy.example.org:9443/", "http://[::1]:19000/"):
            with self.subTest(target=target):
                harness = LauncherHarness()

                def run_server(host, port, callback):
                    self.assertEqual(host, "auto")
                    self.assertIsNone(port)
                    self.assertNotIn("LESHY_PORT", harness.environment)
                    self.assertEqual(harness.timers, [])
                    harness.browser_open.assert_not_called()
                    callback(target)
                    self.assertEqual(len(harness.timers), 1)
                    timer = harness.timers[0]
                    self.assertEqual(timer.delay, 0.1)
                    self.assertTrue(timer.daemon)
                    self.assertTrue(timer.started)
                    harness.browser_open.assert_not_called()
                    self.assertTrue(timer.fire())
                    harness.browser_open.assert_called_once_with(target)

                harness.behavior = run_server
                harness.run()
                self.assertEqual(harness.environment["LESHY_HOST"], "auto")
                self.assertNotIn("LESHY_PORT", harness.environment)
                self.assertEqual(harness.browsers, [])
                self.assertNotIn("leshy.managed_browser", harness.imports)
                self.assertTrue(harness.timers[0].cancelled)
                self.assert_publisher_cleaned(harness)

    def test_cli_env_precedence_and_no_browser_preserve_saved_port_selection(self):
        variants = [
            ({"LESHY_HOST": "::1", "LESHY_PORT": "9211"}, ["--no-browser"], "::1", None, "9211"),
            ({"LESHY_HOST": "127.0.0.1", "LESHY_PORT": "9211"},
             ["--host", "0.0.0.0", "--port", "9312", "--no-browser"], "0.0.0.0", 9312, "9312"),
            ({"LESHY_HTTPS_PORT": "9443"}, ["--no-browser"], "auto", None, None),
        ]
        for platform in ("posix", "nt"):
            for environment, argv, host, port, effective_env in variants:
                with self.subTest(platform=platform, argv=argv, environment=environment):
                    original = dict(environment)
                    harness = LauncherHarness(platform=platform, argv=argv, environment=environment)

                    def run_server(actual_host, actual_port, callback):
                        self.assertEqual((actual_host, actual_port), (host, port))
                        self.assertEqual(harness.environment.get("LESHY_PORT"), effective_env)
                        callback("https://leshy.example.org:9443/")

                    harness.behavior = run_server
                    harness.run()
                    self.assertEqual(environment, original)
                    self.assertEqual(harness.timers, [])
                    self.assertEqual(harness.browsers, [])
                    self.assertNotIn("leshy.managed_browser", harness.imports)
                    harness.browser_open.assert_not_called()
                    self.assertEqual(harness.environment.get("LESHY_HTTPS_PORT"), original.get("LESHY_HTTPS_PORT"))
                    self.assert_publisher_cleaned(harness)

    def test_failure_before_ready_and_no_ready_return_cleanup_and_ignore_late_callback(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                harness = LauncherHarness()

                def run_server(_host, _port, _callback):
                    self.assertEqual(harness.timers, [])
                    harness.browser_open.assert_not_called()
                    if fails:
                        raise RuntimeError("Синтетическая ошибка до готовности")

                harness.behavior = run_server
                if fails:
                    with self.assertRaisesRegex(RuntimeError, "до готовности"):
                        harness.run()
                else:
                    harness.run()
                self.assert_publisher_cleaned(harness)
                harness.serve_calls[0][2]("https://late.example.org:9443/")
                self.assertEqual(harness.timers, [])
                self.assertEqual(harness.browsers, [])
                harness.browser_open.assert_not_called()

    def test_windows_browser_is_lazy_and_cannot_reopen_after_cleanup_or_error(self):
        for outcome in ("normal", "pending_timer", "browser_error", "serve_error"):
            with self.subTest(outcome=outcome):
                harness = LauncherHarness(platform="nt")
                target = "https://leshy.example.org:9443/"
                if outcome == "browser_error":
                    harness.browser_open_error = OSError("Синтетическая ошибка окна")

                def run_server(_host, _port, callback):
                    self.assertEqual(harness.browsers, [])
                    self.assertNotIn("leshy.managed_browser", harness.imports)
                    callback(target)
                    self.assertEqual(harness.browsers, [])
                    if outcome == "pending_timer":
                        raise RuntimeError("Синтетическая ошибка сервера")
                    harness.timers[0].fire()
                    self.assertEqual(len(harness.browsers), 1)
                    self.assertEqual(harness.browsers[0].target, target)
                    if outcome == "serve_error":
                        raise RuntimeError("Синтетическая ошибка сервера")

                harness.behavior = run_server
                if outcome == "browser_error":
                    with self.assertRaisesRegex(OSError, "ошибка окна"):
                        harness.run()
                elif outcome in ("pending_timer", "serve_error"):
                    with self.assertRaisesRegex(RuntimeError, "ошибка сервера"):
                        harness.run()
                else:
                    harness.run()
                self.assert_publisher_cleaned(harness)
                self.assertEqual(len(harness.timers), 1)
                timer = harness.timers[0]
                self.assertTrue(timer.cancelled)
                count = len(harness.browsers)
                if count:
                    harness.browsers[0].open.assert_called_once_with()
                    harness.browsers[0].close.assert_called_once_with()
                self.assertFalse(timer.fire())
                harness.serve_calls[0][2]("https://late.example.org:9443/")
                self.assertEqual(len(harness.timers), 1)
                self.assertEqual(len(harness.browsers), count)
                if count:
                    harness.browsers[0].open.assert_called_once_with()
                harness.browser_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
