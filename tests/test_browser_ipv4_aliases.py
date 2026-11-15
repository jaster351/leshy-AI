"""Регрессии обхода SSRF-фильтра альтернативными записями IPv4.

Не выполнять локально без отдельного разрешения владельца.
xfail(strict=True) фиксирует пока не закрытый риск: браузеры могут
нормализовать IPv4-алиасы иначе, чем urllib.parse.
"""
from __future__ import annotations

import pytest

from leshy.browser_agent import BrowserAction, BrowserActionResult, BrowserAgent, BrowserRisk


class _RecordingAdapter:
    adapter_id = "recording"

    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def execute(self, action):
        self.calls.append(action)
        return BrowserActionResult("ok", "выполнено")


@pytest.mark.xfail(strict=True, reason="Нужна нормализация browser-style IPv4 до проверки host policy")
@pytest.mark.parametrize("host", [
    "2130706433",        # decimal 127.0.0.1
    "0x7f000001",        # hexadecimal 127.0.0.1
    "0177.0.0.1",       # octal 127.0.0.1
    "127.1",            # shortened 127.0.0.1
    "0x7f.0.0.1",       # mixed hexadecimal
])
def test_browser_ipv4_alias_must_not_reach_adapter(host):
    adapter = _RecordingAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction("read", f"http://{host}/", BrowserRisk.READ))
    assert result.status == "blocked"
    assert adapter.calls == []


def test_normal_public_https_host_remains_usable():
    adapter = _RecordingAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction("read", "https://example.com/", BrowserRisk.READ))
    assert result.status == "ok"
    assert len(adapter.calls) == 1
