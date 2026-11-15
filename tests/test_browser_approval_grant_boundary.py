"""Регрессии: boolean approved не является серверным одноразовым разрешением.

До внедрения проверяемого grant негативные сценарии ожидаемо падают.
Не запускать локально без отдельного разрешения владельца.
"""
from __future__ import annotations

import pytest

from leshy.browser_agent import (
    ApprovalRequired, BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk,
)


class RecordingAdapter:
    adapter_id = "approval-boundary-probe"

    def __init__(self):
        self.actions = []

    def available(self):
        return True

    def execute(self, action):
        self.actions.append(action)
        return BrowserActionResult("ok", "выполнено")


@pytest.mark.xfail(strict=True, reason="Issue #85: approved=True пока обходит серверный grant")
@pytest.mark.parametrize("risk", [BrowserRisk.MUTATE, BrowserRisk.COMMIT, BrowserRisk.FINANCIAL])
def test_boolean_approval_alone_must_not_execute_privileged_action(risk):
    adapter = RecordingAdapter()
    agent = BrowserAgent([adapter])
    action = BrowserAction("submit", "https://example.com/checkout", risk)
    try:
        agent.execute(action, approved=True)
    except ApprovalRequired:
        pass
    assert adapter.actions == [], "Нельзя исполнять действие только по boolean approved"


@pytest.mark.xfail(strict=True, reason="Issue #85: grant должен быть привязан к каноническому payload")
def test_boolean_approval_cannot_authorize_modified_payment_payload():
    adapter = RecordingAdapter()
    agent = BrowserAgent([adapter])
    action = BrowserAction(
        "pay", "https://example.com/pay", BrowserRisk.FINANCIAL,
        {"amount": "99999", "recipient": "unverified"},
    )
    try:
        agent.execute(action, approved=True)
    except ApprovalRequired:
        pass
    assert adapter.actions == [], "Подмена суммы/получателя не должна достигать адаптера"


def test_read_without_approval_remains_available():
    adapter = RecordingAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction("read", "https://example.com", BrowserRisk.READ))
    assert result.status == "ok"
    assert len(adapter.actions) == 1
    assert "_approval_verified" not in adapter.actions[0].arguments
