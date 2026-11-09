"""No network access: negative regression for swarm public seed pinning."""
from unittest.mock import patch
import pytest

from leshy.swarm_pinned_transport import _public_addresses, post_seed_hello


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254",
                                "::1", "fc00::1", "fe80::1"])
def test_reject_private_dns(ip):
    answer = [(2, 1, 6, "", (ip, 443))]
    with patch("leshy.swarm_pinned_transport.socket.getaddrinfo", return_value=answer):
        with pytest.raises(ValueError, match="непубличный"):
            _public_addresses("seed.example.org", 443)


def test_mixed_public_private_dns_is_rejected():
    answer = [(2, 1, 6, "", ("8.8.8.8", 443)),
              (2, 1, 6, "", ("127.0.0.1", 443))]
    with patch("leshy.swarm_pinned_transport.socket.getaddrinfo", return_value=answer):
        with pytest.raises(ValueError):
            _public_addresses("seed.example.org", 443)


def test_proxy_fails_closed_before_dns():
    with patch("leshy.swarm_pinned_transport.request.getproxies",
               return_value={"https": "http://proxy.example:8080"}), \
         patch("leshy.swarm_pinned_transport.request.proxy_bypass", return_value=False), \
         patch("leshy.swarm_pinned_transport.socket.getaddrinfo") as dns:
        with pytest.raises(ValueError, match="прокси"):
            post_seed_hello("https://seed.example.org/v1/swarm/hello", b"{}")
        dns.assert_not_called()


def test_pinned_connection_does_not_resolve_dns_again():
    from leshy.swarm_pinned_transport import _PinnedHTTPS
    with patch("leshy.swarm_pinned_transport.socket.create_connection") as connect, \
         patch("leshy.swarm_pinned_transport.ssl.create_default_context") as ctx:
        connection = _PinnedHTTPS("seed.example.org", 443, "8.8.8.8", 2)
        connection.connect()
        connect.assert_called_once_with(("8.8.8.8", 443), 2)
        ctx.return_value.wrap_socket.assert_called_once_with(
            connect.return_value, server_hostname="seed.example.org"
        )


def test_reject_unexpected_path_before_connection():
    with patch("leshy.swarm_pinned_transport.socket.getaddrinfo") as dns:
        with pytest.raises(ValueError):
            post_seed_hello("https://seed.example.org/admin", b"{}")
        dns.assert_not_called()


def test_proxy_bypass_allows_direct_pinned_connection():
    """A platform bypass must not trigger the fail-closed proxy rejection."""
    from leshy.swarm_pinned_transport import _effective_https_proxy
    with patch("leshy.swarm_pinned_transport.request.getproxies",
               return_value={"https": "http://proxy.example:8080"}), \
         patch("leshy.swarm_pinned_transport.request.proxy_bypass", return_value=True) as bypass:
        assert _effective_https_proxy("seed.example.org") is False
        bypass.assert_called_once_with("seed.example.org")
