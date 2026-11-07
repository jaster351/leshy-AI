"""Регрессии политики прямых IP-адресов Browser Agent (без DNS)."""

import pytest

from leshy.browser_agent import BrowserAgent


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://10.1.2.3/", "http://192.168.1.1/",
    "http://172.16.0.1/", "http://169.254.169.254/",
    "http://[::1]/", "http://[fc00::1]/", "http://[fe80::1]/",
    "http://0.0.0.0/",
])
def test_private_ip_literal_blocked_without_explicit_allowlist(url):
    assert not BrowserAgent([])._host_allowed(url)


def test_exact_loopback_allowlist_preserved():
    agent = BrowserAgent([], allowed_hosts=("127.0.0.1",))
    assert agent._host_allowed("http://127.0.0.1:8765/")
    assert not agent._host_allowed("http://127.0.0.2/")


def test_public_https_literal_still_allowed():
    assert BrowserAgent([])._host_allowed("https://8.8.8.8/")
