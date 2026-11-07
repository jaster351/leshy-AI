"""Регрессия обработки отменённого SSE-подключения на Windows (WinError 10053)."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_stream_chat_catches_connection_aborted_before_outer_http_error_handler():
    source = (ROOT / "src" / "leshy" / "web_server_impl.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler_factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "make_handler")
    handler = next(node for node in handler_factory.body if isinstance(node, ast.ClassDef) and node.name == "Handler")
    stream = next(node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name == "_stream_chat")
    catches = [node for node in ast.walk(stream) if isinstance(node, ast.ExceptHandler)]
    covered = {
        name.id
        for node in catches
        if isinstance(node.type, ast.Tuple)
        for name in node.type.elts
        if isinstance(name, ast.Name)
    }
    assert {"BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError"} <= covered
    # The disconnect handler should stop the exact request rather than returning HTTP JSON.
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "stop" for node in ast.walk(stream))
