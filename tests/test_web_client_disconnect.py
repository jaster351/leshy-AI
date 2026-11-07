"""Обрыв обычного HTTP-ответа не должен превращаться в traceback сервера.

Проверка структуры здесь дополняет локальный runtime smoke-test: socketserver
не перехватывает исключение после выхода из обработчика.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "leshy" / "web_server_impl.py"


def _handler_method(name: str):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "make_handler")
    handler = next(node for node in factory.body if isinstance(node, ast.ClassDef) and node.name == "Handler")
    return next(node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name == name)


def test_handler_catches_expected_client_disconnects():
    handler = _handler_method("handle")
    catches = {
        name.id
        for node in ast.walk(handler)
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple)
        for name in node.type.elts if isinstance(name, ast.Name)
    }
    assert {"ConnectionAbortedError", "ConnectionResetError", "BrokenPipeError"} <= catches


def test_json_still_serializes_and_writes_data():
    json_method = _handler_method("_json")
    calls = [
        node.func.attr
        for node in ast.walk(json_method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "dumps" in calls
    assert "write" in calls


def test_unrelated_exceptions_are_not_silenced():
    method = _handler_method("handle")
    exceptions = [node for node in ast.walk(method) if isinstance(node, ast.ExceptHandler)]
    assert len(exceptions) == 1
    assert isinstance(exceptions[0].type, ast.Tuple)
