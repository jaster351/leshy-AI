from __future__ import annotations

from pathlib import Path

from leshy import web_server_impl7
from leshy.telegram_fast import FastTelegramBridge


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_html_uses_reference_chat_transport_without_extra_chat_owners(monkeypatch):
    reference = b'<html><body><script src="/chat-multitask.js"></script></body></html>'
    monkeypatch.setattr(web_server_impl7.shell, "_runtime_html", lambda _settings: reference)
    rendered = web_server_impl7._runtime_html(object())
    assert rendered == reference
    text = rendered.decode("utf-8")
    assert '/chat-multitask.js' in text
    assert '/chat-controller-canonical.js' not in text
    assert '/chat-idempotency.js' not in text
    assert '/chat-stall-watchdog.js' not in text
    assert '/chat-followup-finalizer.js' not in text


def test_chat_multitask_matches_reference_send_lifecycle():
    js = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
    assert "const streams=new Map();" in js
    assert "activity:'Леший думает…'" in js
    assert "const ui=makeStreamingBox('');" in js
    assert "runStream(text,requested);" in js
    assert "return await runStream(text,requested)" not in js
    assert "userText:text" not in js
    assert "request_id:requestId" in js


def test_reference_telegram_timing_is_restored_without_removing_new_features():
    web_server_impl7._restore_reference_chat_timing()
    assert FastTelegramBridge.edit_interval_seconds == 0.55
    bridge = object.__new__(FastTelegramBridge)
    assert bridge._wait_for_foreground_slot() is None
    assert hasattr(FastTelegramBridge, "_finalize_answer")


def test_full_layout_injects_sidebar_only_once(monkeypatch):
    reference = b'<html><body><aside class="sidebar"></aside><section id="pane-chat"></section></body></html>'
    monkeypatch.setattr(web_server_impl7.shell, "_runtime_html", lambda _settings: reference)
    rendered = web_server_impl7._runtime_html(object()).decode("utf-8")
    for asset in ("sidebar-session-actions.js", "chat-layout-status.js", "module-settings-panel.js"):
        assert rendered.count(f'<script src="/{asset}"></script>') == 1
    assert "chat-controller-canonical.js" not in rendered
