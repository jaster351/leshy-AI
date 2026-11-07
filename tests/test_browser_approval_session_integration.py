"""Сквозные регрессии сессий Browser Approval без реального браузера/сети."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from leshy.browser_agent import BrowserAction, BrowserActionResult, BrowserRisk
from leshy.browser_platform_runtime import BrowserConversationIntent, BrowserPlatform
from leshy.storage import Storage


class _CaptureAgent:
    def __init__(self):
        self.calls = []

    def execute(self, action, approved=False):
        self.calls.append((action, approved))
        return BrowserActionResult("ok", "Выполнено", {})


@pytest.fixture
def prepared(tmp_path):
    app = SimpleNamespace(storage=Storage(tmp_path / "test.db"), llm=object())
    with app.storage.connection() as conn:
        for sid, owner in (
            ("session-a", "owner-a"),
            ("session-a2", "owner-a"),
            ("session-b", "owner-b"),
        ):
            conn.execute(
                "INSERT INTO sessions(id,owner_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                (sid, owner, "Тест", "2026-11-05", "2026-11-05"),
            )
    platform = BrowserPlatform(app)
    capture = _CaptureAgent()
    platform.agent = capture
    platform._remember_results(
        "session-a",
        [{"title": "Вариант", "url": "https://example.org/checkout", "snippet": ""}],
    )
    intent = BrowserConversationIntent("commit", "Забронируй первый", selected_index=0)
    return platform, capture, intent, app


def test_same_owner_other_chat_cannot_consume_or_cancel(prepared):
    platform, capture, intent, _ = prepared
    pending = platform.request_external_action(intent, owner_id="owner-a", session_id="session-a")
    assert pending["session_id"] == "session-a"
    assert platform.approvals.list("owner-a", session_id="session-a2") == []
    with pytest.raises(KeyError):
        platform.resolve_approval("owner-a", pending["approval_id"], session_id="session-a2", approved=True)
    with pytest.raises(KeyError):
        platform.resolve_approval("owner-a", pending["approval_id"], session_id="session-a2", approved=False)
    assert not capture.calls
    result = platform.resolve_approval("owner-a", pending["approval_id"], session_id="session-a", approved=True)
    assert result.status == "ok"
    assert len(capture.calls) == 1
    assert capture.calls[0][1] is True
    with pytest.raises(KeyError):
        platform.resolve_approval("owner-a", pending["approval_id"], session_id="session-a", approved=True)
    assert len(capture.calls) == 1


def test_foreign_owner_cannot_use_same_session_id(prepared):
    platform, capture, intent, _ = prepared
    pending = platform.request_external_action(intent, owner_id="owner-a", session_id="session-a")
    with pytest.raises(PermissionError):
        platform.resolve_approval("owner-b", pending["approval_id"], session_id="session-a", approved=True)
    with pytest.raises(PermissionError):
        platform.request_external_action(intent, owner_id="owner-b", session_id="session-a")
    assert not capture.calls
    assert platform.approvals.list("owner-a", session_id="session-a")


def test_deleted_session_invalidates_pending_approval(prepared):
    platform, capture, intent, app = prepared
    pending = platform.request_external_action(intent, owner_id="owner-a", session_id="session-a")
    with app.storage.connection() as conn:
        conn.execute("DELETE FROM sessions WHERE id=?", ("session-a",))
    with pytest.raises(PermissionError):
        platform.resolve_approval("owner-a", pending["approval_id"], session_id="session-a", approved=True)
    assert capture.calls == []


def test_legacy_unbound_approval_not_executable_through_bound_runtime(prepared):
    platform, capture, _intent, _ = prepared
    item = platform.approvals.request(
        "owner-a", BrowserAction("agent_commit", "https://example.org", BrowserRisk.COMMIT)
    )
    assert platform.approvals.list("owner-a", session_id="session-a") == []
    with pytest.raises(KeyError):
        platform.resolve_approval("owner-a", item.approval_id, session_id="session-a", approved=True)
    assert not capture.calls


@pytest.mark.parametrize("owner,session", [
    ("web:local", "session-a"),
    ("owner-a", ""),
    ("owner-a", "does-not-exist"),
    ("", "session-a"),
])
def test_unsafe_identity_or_nonexistent_session_fails_closed(prepared, owner, session):
    platform, capture, intent, _ = prepared
    with pytest.raises(PermissionError):
        platform.request_external_action(intent, owner_id=owner, session_id=session)
    assert not capture.calls
