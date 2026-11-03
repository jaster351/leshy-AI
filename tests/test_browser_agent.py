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


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "ftp://example.com/file",
    "https://example.com:8443/admin",
    "http://example.com:8080/",
    "https://user:password@example.com/",
    "https://[invalid/",
    "https://",
])
def test_unsafe_browser_url_is_blocked_even_without_allowlist(url):
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction("read", url, BrowserRisk.READ))
    assert result.status == "blocked"
    assert adapter.calls == []


def test_explicit_loopback_allowlist_permits_local_ephemeral_port():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter], allowed_hosts=("127.0.0.1",))
    assert agent.execute(BrowserAction("read", "http://127.0.0.1:39123/", BrowserRisk.READ)).status == "ok"
    assert len(adapter.calls) == 1


def test_loopback_nonstandard_port_without_explicit_allowlist_is_blocked():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    assert agent.execute(BrowserAction("read", "http://127.0.0.1:39123/", BrowserRisk.READ)).status == "blocked"
    assert adapter.calls == []


@pytest.mark.parametrize("url", [
    "https://example.com",
    "https://example.com:443/path",
    "http://example.com:80/path",
])
def test_standard_http_https_urls_remain_allowed(url):
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    assert agent.execute(BrowserAction("read", url, BrowserRisk.READ)).status == "ok"
