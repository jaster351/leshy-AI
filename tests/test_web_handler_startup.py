"""Два изолированных сценария перекрывающихся запусков публичного Web entry point.

Запуск требует отдельного разрешения владельца. Из AST берутся реальные serve
и listener_ready: тяжёлые модули, LeshyApp, сокеты, TLS и CA не импортируются.
Два короткоживущих потока исполняют только подменённый core и ContextVar.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Callable
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "src" / "leshy"


def function_from_source(filename, name, namespace):
    """Исполнить только объявление указанной функции из текущего исходника."""
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, str(SOURCE / filename), "exec"), namespace)
    return namespace[name]


class WebHandlerStartupTests(unittest.TestCase):
    def run_overlap(self, failing):
        marker = object()
        factory = Mock(return_value=marker)
        previous_factory = object()
        previous = SimpleNamespace(make_handler=previous_factory,
                                   serve=Mock(side_effect=AssertionError("Старая serve-цепочка запрещена")))
        callback_context = ContextVar("isolated_listener_callback", default=None)
        scope = {"contextmanager": contextmanager, "_listener_callback": callback_context,
                 "Callable": Callable}
        listener_ready = function_from_source("web_transport_runtime.py", "listener_ready", scope)
        barrier = threading.Barrier(2, timeout=3)
        lock = threading.Lock()
        observations = []
        failures = []
        results = []
        prepare = Mock()

        def core_serve(*, host, port, handler_factory):
            self.assertIs(handler_factory, factory)
            self.assertIs(previous.make_handler, previous_factory)
            self.assertIs(handler_factory(host, port), marker)
            barrier.wait()
            callback = callback_context.get()
            callback(host)
            with lock:
                observations.append((host, port))
            if host == failing:
                raise ValueError("Синтетический отказ одного запуска")

        namespace = {"previous": previous, "core": SimpleNamespace(serve=core_serve),
                     "make_handler": factory, "prepare_runtime": prepare,
                     "listener_ready": listener_ready, "Callable": Callable}
        serve = function_from_source("web_server_impl18.py", "serve", namespace)

        def worker(host, port):
            local = []
            try:
                serve(host=host, port=port, on_listening=local.append)
            except BaseException as error:
                with lock:
                    failures.append((host, error))
            finally:
                with lock:
                    results.append((host, local, callback_context.get()))

        workers = [threading.Thread(target=worker, args=("first", 18001)),
                   threading.Thread(target=worker, args=("second", 18002))]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join(timeout=5)
        self.assertTrue(all(not item.is_alive() for item in workers))
        self.assertCountEqual(observations, [("first", 18001), ("second", 18002)])
        self.assertCountEqual(results, [("first", ["first"], None), ("second", ["second"], None)])
        self.assertIsNone(callback_context.get())
        self.assertIs(previous.make_handler, previous_factory)
        previous.serve.assert_not_called()
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(factory.call_count, 2)
        if failing:
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0][0], failing)
            self.assertIsInstance(failures[0][1], ValueError)
        else:
            self.assertEqual(failures, [])

    def test_concurrent_starts_keep_factory_and_callbacks_local(self):
        self.run_overlap(failing=None)

    def test_failed_concurrent_start_restores_only_its_callback(self):
        self.run_overlap(failing="first")


if __name__ == "__main__":
    unittest.main()
