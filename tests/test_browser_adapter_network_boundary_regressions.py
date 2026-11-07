"""Регрессии сетевой границы браузерного адаптера (без запуска реального браузера).

Сценарии xfail(strict=True) описывают ещё не реализованную проверку
фактического IP и каждого перенаправления. Нельзя считать SSRF закрытым,
пока они не переведены в обычные проходящие тесты и не проверен E2E.
"""
from __future__ import annotations

import pytest

from leshy.playwright_browser_adapter import PlaywrightBrowserAdapter


@pytest.mark.parametrize("destination", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://10.0.0.7/internal",
])
@pytest.mark.xfail(strict=True, reason="Адаптер пока не проверяет каждый redirect target по сетевой политике")
def test_redirect_target_must_not_bypass_public_origin(destination):
    # Проверка должна происходить в runtime до запроса redirect target,
    # а не только после загрузки страницы или при разборе исходного URL.
    assert hasattr(PlaywrightBrowserAdapter, "_validate_navigation_target")
    assert not PlaywrightBrowserAdapter._validate_navigation_target(
        destination, allowed_hosts=("example.com",)
    )


@pytest.mark.xfail(strict=True, reason="Нет обязательной проверки DNS/IP перед подключением")
def test_public_hostname_resolving_to_loopback_is_denied():
    assert hasattr(PlaywrightBrowserAdapter, "_validate_resolved_addresses")
    assert not PlaywrightBrowserAdapter._validate_resolved_addresses(
        "example.com", ["127.0.0.1"], allowed_hosts=("example.com",)
    )


@pytest.mark.xfail(strict=True, reason="Нет обязательной проверки смешанных DNS-ответов")
def test_mixed_public_and_private_dns_answers_are_denied():
    assert hasattr(PlaywrightBrowserAdapter, "_validate_resolved_addresses")
    assert not PlaywrightBrowserAdapter._validate_resolved_addresses(
        "example.com", ["93.184.215.14", "10.1.2.3"], allowed_hosts=("example.com",)
    )


@pytest.mark.xfail(strict=True, reason="Не подтверждена проверка DNS rebinding непосредственно перед соединением")
def test_dns_rebinding_requires_connection_time_enforcement():
    assert hasattr(PlaywrightBrowserAdapter, "_validate_connection_peer")
    assert not PlaywrightBrowserAdapter._validate_connection_peer(
        "example.com", "127.0.0.1", allowed_hosts=("example.com",)
    )
