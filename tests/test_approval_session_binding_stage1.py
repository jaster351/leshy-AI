"""Регрессии привязки Human Approval к сессии (без запуска локально)."""

import pytest

from leshy.action_approvals import ApprovalBroker
from leshy.browser_agent import BrowserAction, BrowserRisk


def _action():
    return BrowserAction("submit", "https://example.org", BrowserRisk.MUTATE, {}, description="Подтвердить")


def test_bound_approval_rejects_other_session_and_missing_session():
    broker = ApprovalBroker()
    item = broker.request("owner-a", _action(), session_id="session-a")
    for session in ("session-b", None):
        with pytest.raises(KeyError):
            broker.consume("owner-a", item.approval_id, session_id=session)
    assert broker.consume("owner-a", item.approval_id, session_id="session-a").name == "submit"
    with pytest.raises(KeyError):
        broker.consume("owner-a", item.approval_id, session_id="session-a")


def test_bound_approval_is_hidden_and_not_denied_by_other_session():
    broker = ApprovalBroker()
    item = broker.request("owner-a", _action(), session_id="session-a")
    assert broker.list("owner-a", session_id="session-b") == []
    assert broker.list("owner-b", session_id="session-a") == []
    with pytest.raises(KeyError):
        broker.deny("owner-a", item.approval_id, session_id="session-b")
    broker.deny("owner-a", item.approval_id, session_id="session-a")


def test_legacy_unbound_approval_still_works():
    broker = ApprovalBroker()
    item = broker.request("owner-a", _action())
    assert broker.consume("owner-a", item.approval_id).name == "submit"
