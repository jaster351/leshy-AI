"""Регрессии сетевой границы Browser Agent (без выполнения сетевых запросов).

Тесты xfail фиксируют известный пробел: при пустом allowlist приватные
IP-адреса сейчас проходят URL-проверку. Это не подтверждение исправления.
DNS rebinding и редиректы требуют отдельной проверки на уровне адаптера.
"""
from __future__ import annotations

import pytest

from leshy.browser_agent import BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk


class RecordingAdapter:
    adapter_id = "recording"

    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def execute(self, action):
        self.calls.append(action)
        return BrowserActionResult("ok", "executed")


@pytest.mark.xfail(
    strict=True,
    reason="Известный пробел: без allowlist BrowserAgent пропускает private/loopback IP",
)
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[fc00::1]/",
    "http://[fe80::1]/",
])
def test_private_network_literal_denied_by_default(url):
    adapter = RecordingAdapter()
    result = BrowserAgent([adapter]).execute(BrowserAction("read", url, BrowserRisk.READ))
    assert result.status == "blocked"
    assert adapter.calls == []


def test_explicit_loopback_allowlist_remains_supported():
    adapter = RecordingAdapter()
    agent = BrowserAgent([adapter], allowed_hosts=("127.0.0.1",))
    result = agent.execute(BrowserAction("read", "http://127.0.0.1:39123/", BrowserRisk.READ))
    assert result.status == "ok"
    assert len(adapter.calls) == 1


def test_public_url_not_blocked_by_private_network_policy():
    adapter = RecordingAdapter()
    result = BrowserAgent([adapter]).execute(
        BrowserAction("read", "https://example.com/page", BrowserRisk.READ)
    )
    assert result.status == "ok"
    assert len(adapter.calls) == 1
