"""Offline URL-validation regressions for the public swarm hello transport.

These tests deliberately do not perform network access. They verify rejection
occurs before DNS resolution, proxy inspection or socket creation.
"""
from unittest.mock import patch

import pytest

from leshy.swarm_pinned_transport import post_seed_hello


@pytest.mark.parametrize("url", [
    "http://seed.example.org/v1/swarm/hello",
    "https://localhost/v1/swarm/hello",
    "https://127.0.0.1/v1/swarm/hello",
    "https://[::1]/v1/swarm/hello",
    "https://user:pass@seed.example.org/v1/swarm/hello",
    "https://seed.example.org/admin",
    "https://seed.example.org/v1/swarm/hello?next=internal",
    "https://seed.example.org/v1/swarm/hello#fragment",
    "https://seed.example.org/v1/swarm/hello/",
])
def test_invalid_public_hello_url_rejected_before_io(url):
    with patch("leshy.swarm_pinned_transport.socket.getaddrinfo") as dns, \
         patch("leshy.swarm_pinned_transport.socket.create_connection") as dial, \
         patch("leshy.swarm_pinned_transport.request.getproxies") as proxies:
        with pytest.raises(ValueError):
            post_seed_hello(url, b"{}", allow_local_http=False)
        dns.assert_not_called()
        dial.assert_not_called()
        proxies.assert_not_called()


def test_valid_public_hello_reaches_proxy_policy_before_dns():
    with patch("leshy.swarm_pinned_transport._effective_https_proxy", return_value=True) as proxy, \
         patch("leshy.swarm_pinned_transport.socket.getaddrinfo") as dns:
        with pytest.raises(ValueError, match="прокси"):
            post_seed_hello("https://seed.example.org/v1/swarm/hello", b"{}")
        proxy.assert_called_once_with("seed.example.org")
        dns.assert_not_called()
