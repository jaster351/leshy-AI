"""Offline regressions: pinned swarm transport closes sockets on failures.

These checks do not initiate network access and do not assert CI success.
"""
from unittest.mock import patch

import pytest

from leshy.swarm_pinned_transport import _PinnedHTTPS, post_seed_hello


def test_tls_handshake_failure_closes_pinned_socket():
    with patch("leshy.swarm_pinned_transport.socket.create_connection") as dial, \
         patch("leshy.swarm_pinned_transport.ssl.create_default_context") as context:
        context.return_value.wrap_socket.side_effect = OSError("TLS handshake failed")
        conn = _PinnedHTTPS("seed.example.org", 443, "8.8.8.8", 2)
        with pytest.raises(OSError, match="TLS handshake"):
            conn.connect()
        dial.assert_called_once_with(("8.8.8.8", 443), 2)
        context.return_value.wrap_socket.assert_called_once_with(
            dial.return_value, server_hostname="seed.example.org"
        )
        dial.return_value.close.assert_called_once()


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 503])
def test_public_hello_rejects_non_200_without_redirect(status):
    with patch("leshy.swarm_pinned_transport._effective_https_proxy", return_value=False), \
         patch("leshy.swarm_pinned_transport._public_addresses", return_value=("8.8.8.8",)), \
         patch("leshy.swarm_pinned_transport._PinnedHTTPS") as connection:
        connection.return_value.getresponse.return_value.status = status
        with pytest.raises(ValueError, match="отклонил"):
            post_seed_hello("https://seed.example.org/v1/swarm/hello", b"{}")
        connection.return_value.close.assert_called_once()
        connection.return_value.getresponse.return_value.read.assert_not_called()


def test_public_hello_reads_only_limit_plus_one():
    with patch("leshy.swarm_pinned_transport._effective_https_proxy", return_value=False), \
         patch("leshy.swarm_pinned_transport._public_addresses", return_value=("8.8.8.8",)), \
         patch("leshy.swarm_pinned_transport._PinnedHTTPS") as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b"x" * 17
        with pytest.raises(ValueError, match="превышает"):
            post_seed_hello("https://seed.example.org/v1/swarm/hello",
                            b"{}", max_bytes=16)
        response.read.assert_called_once_with(17)
        connection.return_value.close.assert_called_once()


def test_local_hello_rejects_oversized_response():
    with patch("leshy.swarm_pinned_transport._PinnedLoopbackHTTP") as connection:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b"x" * 17
        with pytest.raises(ValueError, match="превышает"):
            post_seed_hello("http://localhost/v1/swarm/hello", b"{}",
                            max_bytes=16, allow_local_http=True)
        response.read.assert_called_once_with(17)
        connection.return_value.close.assert_called_once()
