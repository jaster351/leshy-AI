"""Offline regression: dev HTTP pilot must never resolve localhost via DNS."""
from unittest.mock import patch

import pytest

from leshy.swarm_pinned_transport import _PinnedLoopbackHTTP, post_seed_hello


@pytest.mark.parametrize("host,expected", [
    ("localhost", "127.0.0.1"),
    ("127.0.0.1", "127.0.0.1"),
    ("::1", "::1"),
])
def test_loopback_transport_uses_numeric_address(host, expected):
    with patch("leshy.swarm_pinned_transport.socket.create_connection") as connect, \
         patch("leshy.swarm_pinned_transport.socket.getaddrinfo") as dns:
        conn = _PinnedLoopbackHTTP(host, 8765, timeout=3)
        conn.connect()
        connect.assert_called_once_with((expected, 8765), 3)
        dns.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://localhost:8765/v1/swarm/hello",
    "http://127.0.0.1:8765/v1/swarm/hello",
    "http://[::1]:8765/v1/swarm/hello",
])
def test_local_hello_never_uses_dns_or_urllib(url):
    with patch("leshy.swarm_pinned_transport.socket.create_connection") as connect, \
         patch("leshy.swarm_pinned_transport.socket.getaddrinfo") as dns, \
         patch("leshy.swarm_pinned_transport.request.build_opener") as opener, \
         patch("leshy.swarm_pinned_transport._PinnedLoopbackHTTP.getresponse") as response:
        response.return_value.status = 200
        response.return_value.read.return_value = b"{}"
        assert post_seed_hello(url, b"{}", allow_local_http=True) == b"{}"
        connect.assert_called_once()
        dns.assert_not_called()
        opener.assert_not_called()
