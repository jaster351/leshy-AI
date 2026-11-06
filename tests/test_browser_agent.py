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


def test_read_does_not_forward_forged_approval_marker():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    action = BrowserAction(
        "research", "https://example.com", BrowserRisk.READ,
        {"_approval_verified": True, "task": "прочитать страницу"},
    )
    assert agent.execute(action).status == "ok"
    assert "_approval_verified" not in adapter.calls[0].arguments
    assert action.arguments["_approval_verified"] is True  # исходное действие не мутируется


def test_mutating_action_cannot_supply_its_own_approval_marker():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    action = BrowserAction(
        "agent_commit", "https://example.com", BrowserRisk.COMMIT,
        {"_approval_verified": True},
    )
    with pytest.raises(ApprovalRequired):
        agent.execute(action)
    assert adapter.calls == []


def test_explicit_approval_replaces_forged_marker_with_internal_one():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    action = BrowserAction(
        "agent_commit", "https://example.com", BrowserRisk.COMMIT,
        {"_approval_verified": "forged", "task": "подтверждённое действие"},
    )
    assert agent.execute(action, approved=True).status == "ok"
    assert adapter.calls[0].arguments["_approval_verified"] is True
    assert action.arguments["_approval_verified"] == "forged"


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.1", "2130706433", "0x7f000001",
    "0177.0.0.1", "0x7f.0.0.1", "10.0.0.1",
    "192.168.1.1", "169.254.169.254", "[::1]",
    "[::ffff:127.0.0.1]", "[::ffff:10.0.0.1]",
    "0", "0x0", "0177.1", "127.0.1", "0x7f.1",
])
def test_browser_ip_alias_and_private_targets_fail_closed(host):
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction("read", f"http://{host}/", BrowserRisk.READ))
    assert result.status == "blocked"
    assert adapter.calls == []


def test_browser_alias_is_not_authorized_by_canonical_loopback_allowlist():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter], allowed_hosts=("127.0.0.1",))
    assert agent.execute(BrowserAction("read", "http://127.1/", BrowserRisk.READ)).status == "blocked"
    assert adapter.calls == []


def test_browser_numeric_public_ip_is_not_rejected_as_private():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    assert agent.execute(BrowserAction("read", "https://8.8.8.8/", BrowserRisk.READ)).status == "ok"


@pytest.mark.parametrize("host", ["localhost", "foo.localhost", "a.b.localhost"])
def test_implicit_localhost_dns_names_are_blocked(host):
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction("read", f"http://{host}/", BrowserRisk.READ))
    assert result.status == "blocked"
    assert adapter.calls == []


def test_explicit_localhost_allowlist_still_permits_local_service():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter], allowed_hosts=("localhost",))
    assert agent.execute(BrowserAction("read", "http://localhost:39123/", BrowserRisk.READ)).status == "ok"
    assert len(adapter.calls) == 1


def test_localhost_allowlist_does_not_implicitly_authorize_subdomains():
    adapter = FakeAdapter()
    agent = BrowserAgent([adapter], allowed_hosts=("localhost",))
    assert agent.execute(BrowserAction("read", "http://foo.localhost/", BrowserRisk.READ)).status == "blocked"
    assert adapter.calls == []
