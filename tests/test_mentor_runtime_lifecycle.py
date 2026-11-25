"""Guard the actual Windows/web startup path, not the retired implementation."""
import ast
from pathlib import Path

from leshy.mentor_bridge import MentorBridge

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_server_starts_and_stops_mentor():
    tree = ast.parse((ROOT / "src/leshy/web_server_impl3.py").read_text(encoding="utf-8"))
    serve = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "serve")
    calls = [n for n in ast.walk(serve) if isinstance(n, ast.Call)]
    assert any(isinstance(n.func, ast.Name) and n.func.id == "MentorBridge"
               and any(k.arg == "ready_event" and isinstance(k.value, ast.Name)
                       and k.value.id == "warmup_ready" for k in n.keywords) for n in calls)
    assert any(isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "mentor_bridge" and n.func.attr == "start" for n in calls)
    assert any(isinstance(n, ast.Try) and any(isinstance(c, ast.Call)
               and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
               and c.func.value.id == "mentor_bridge" and c.func.attr == "stop"
               for stmt in n.finalbody for c in ast.walk(stmt)) for n in ast.walk(serve))


def test_mentor_waits_for_warmup_without_starting_runner(tmp_path, monkeypatch):
    import threading
    from leshy import mentor_bridge

    ready = threading.Event()
    bridge = MentorBridge(tmp_path, object(), ready_event=ready)
    waits = []

    def wait(seconds):
        waits.append(seconds)
        return len(waits) > 1

    monkeypatch.setattr(bridge.stop_event, "wait", wait)

    def forbidden(*args, **kwargs):
        raise AssertionError("Runner started before warmup")

    monkeypatch.setattr(mentor_bridge, "BatchRunner", forbidden)
    bridge._run()
    assert waits == [15, 1]
