"""Offline DNS pinning regressions for public swarm hello.

These tests never open a real socket. They deliberately cover mixed A/AAAA
answers and verify the selected address is passed to the pinned connection.
"""
from unittest.mock import patch

import pytest

from leshy.swarm_pinned_transport import _public_addresses, post_seed_hello


def _answer(ip):
    family = 10 if ":" in ip else 2
    return (family, 1, 6, "", (ip, 443))


@pytest.mark.parametrize("addresses", [
    ["8.8.8.8", "127.0.0.1"],
    ["8.8.8.8", "10.0.0.1"],
    ["2606:4700:4700::1111", "::1"],
    ["8.8.8.8", "fe80::1"],
    ["8.8.8.8", "169.254.1.2"],
])
def test_mixed_dns_answer_is_rejected_wholesale(addresses):
    with patch("leshy.swarm_pinned_transport.socket.getaddrinfo",
               return_value=[_answer(ip) for ip in addresses]):
        with pytest.raises(ValueError, match="непубличный"):
            _public_addresses("seed.example.org", 443)


def test_empty_dns_answer_is_rejected():
    with patch("leshy.swarm_pinned_transport.socket.getaddrinfo", return_value=[]):
        with pytest.raises(ValueError, match="не вернул"):
            _public_addresses("seed.example.org", 443)


def test_duplicate_public_dns_answers_are_deduplicated_in_order():
    with patch("leshy.swarm_pinned_transport.socket.getaddrinfo",
               return_value=[_answer(ip) for ip in
                             ("8.8.8.8", "1.1.1.1", "8.8.8.8")]):
        assert _public_addresses("seed.example.org", 443) == ("8.8.8.8", "1.1.1.1")


def test_hello_uses_selected_ip_without_a_second_dns_lookup():
    with patch("leshy.swarm_pinned_transport._effective_https_proxy", return_value=False), \
         patch("leshy.swarm_pinned_transport.socket.getaddrinfo",
               return_value=[_answer("8.8.8.8")]) as dns, \
         patch("leshy.swarm_pinned_transport._PinnedHTTPS") as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"ok":true}'
        result = post_seed_hello("https://seed.example.org/v1/swarm/hello", b"{}")
        assert result == b'{"ok":true}'
        dns.assert_called_once_with("seed.example.org", 443, type=1)
        connection.assert_called_once_with("seed.example.org", 443, "8.8.8.8", 5.0)
        connection.return_value.close.assert_called_once()
