"""Regression coverage for effective HTTPS proxy selection (no network calls)."""
from unittest.mock import patch

from leshy.swarm_pinned_transport import _effective_https_proxy


def test_no_proxy_only_does_not_block_public_seed():
    with patch("leshy.swarm_pinned_transport.request.getproxies", return_value={"no": "example.org"}):
        assert not _effective_https_proxy("seed.example.org")


def test_http_only_proxy_does_not_block_https():
    with patch("leshy.swarm_pinned_transport.request.getproxies", return_value={"http": "http://proxy:8080"}):
        assert not _effective_https_proxy("seed.example.org")


def test_https_proxy_without_bypass_blocks():
    with patch("leshy.swarm_pinned_transport.request.getproxies", return_value={"https": "http://proxy:8080"}), patch(
        "leshy.swarm_pinned_transport.request.proxy_bypass", return_value=False
    ):
        assert _effective_https_proxy("seed.example.org")


def test_all_proxy_without_bypass_blocks():
    with patch("leshy.swarm_pinned_transport.request.getproxies", return_value={"all": "socks5://proxy:1080"}), patch(
        "leshy.swarm_pinned_transport.request.proxy_bypass", return_value=False
    ):
        assert _effective_https_proxy("seed.example.org")


def test_https_proxy_with_platform_bypass_allows_direct_pinned_connection():
    with patch("leshy.swarm_pinned_transport.request.getproxies", return_value={"https": "http://proxy:8080"}), patch(
        "leshy.swarm_pinned_transport.request.proxy_bypass", return_value=True
    ):
        assert not _effective_https_proxy("seed.example.org")
