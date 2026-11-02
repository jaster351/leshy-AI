from __future__ import annotations

import pytest

from leshy.browser_agent import ApprovalRequired, BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk


class FakeAdapter:
    adapter_id = "fake"

    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def execute(self, action):
        self.calls.append(action)
        return BrowserActionResult("ok", "готово", {"action": action.name})


def test_read_action_runs_without_approval():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction("search", "https://example.com", BrowserRisk.READ))
    assert result.status == "ok"
    assert len(adapter.calls) == 1


def test_financial_action_requires_explicit_approval():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    action = BrowserAction("pay", "https://shop.example.com/checkout", BrowserRisk.FINANCIAL, description="Оплатить заказ")
    with pytest.raises(ApprovalRequired):
        agent.execute(action)
    assert adapter.calls == []
    assert agent.execute(action, approved=True).status == "ok"


def test_mutating_action_requires_approval_too():
    agent = BrowserAgent([FakeAdapter()])
    with pytest.raises(ApprovalRequired):
        agent.execute(BrowserAction("submit", "https://example.com/form", BrowserRisk.MUTATE))


def test_allowlist_blocks_untrusted_redirect_target():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter], allowed_hosts=("example.com",))
    result = agent.execute(BrowserAction("read", "https://evil.invalid", BrowserRisk.READ))
    assert result.status == "blocked"
    assert adapter.calls == []


def test_allowlist_accepts_subdomains():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter], allowed_hosts=("example.com",))
    result = agent.execute(BrowserAction("read", "https://shop.example.com/item", BrowserRisk.READ))
    assert result.status == "ok"
