from __future__ import annotations

from pathlib import Path

from leshy import chat_activity_runtime, web_server_impl7
from leshy.realtime_chat import StreamEvent


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_page_injects_visibility_fix_without_replacing_chat_transport(monkeypatch):
    base = b'<html><body><script src="/chat-multitask.js"></script></body></html>'
    monkeypatch.setattr(web_server_impl7.shell, "_runtime_html", lambda _settings: base)
    rendered = web_server_impl7._runtime_html(object()).decode("utf-8")
    assert '/chat-multitask.js' in rendered
    assert '/chat-visibility-fix.js' in rendered
    assert '/sidebar-session-actions.js' in rendered
    assert '/chat-controller-canonical.js' not in rendered


def test_visibility_fix_uses_real_composer_height_and_forces_done_scroll():
    js = (ROOT / "web" / "chat-visibility-fix.js").read_text(encoding="utf-8")
    assert "ResizeObserver" in js
    assert "--leshy-chat-safe-bottom" in js
    assert "rect.height" in js
    assert "window.addEventListener('leshy:done'" in js
    assert "root.scrollTop=root.scrollHeight" in js
    assert "sendFrom" not in js
    assert "/v1/chat/stream" not in js


def test_activity_runtime_reports_busy_model(monkeypatch):
    class Scheduler:
        def status(self):
            return {"active": {"source": "telegram:42"}, "waiting": [], "queue_depth": 0}

    class DummyApp:
        inference_scheduler = Scheduler()

    manager = object.__new__(chat_activity_runtime.RealtimeChatManager)
    manager.app = DummyApp()

    original = chat_activity_runtime._ORIGINAL_STREAM
    installed = chat_activity_runtime._INSTALLED
    try:
        chat_activity_runtime._INSTALLED = False

        def fake_original(self, message, *, owner_id="web:local", session_id=None, request_id=None):
            yield StreamEvent("done", {"answer": "ok"})

        chat_activity_runtime._ORIGINAL_STREAM = fake_original
        # Проверяем непосредственно контракт обёртки через временную установку.
        chat_activity_runtime.install_chat_activity_runtime()
        items = list(chat_activity_runtime.RealtimeChatManager.stream(manager, "test", owner_id="web:local"))
        assert items[0].type == "activity"
        assert "Жду освобождения модели" in items[0].data["text"]
        assert "Telegram" in items[0].data["text"]
    finally:
        # Тестовый процесс не должен оставлять monkey-patch в неожиданном состоянии.
        chat_activity_runtime._INSTALLED = installed
        if original is not None:
            chat_activity_runtime.RealtimeChatManager.stream = original
            chat_activity_runtime._ORIGINAL_STREAM = original
