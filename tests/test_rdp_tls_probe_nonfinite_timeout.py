"""Reject non-finite RDP timeouts before any socket is opened."""
import math
from unittest.mock import patch

import pytest

from leshy.rdp_tls_probe import probe_rdp_tls


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_timeout_never_connects(timeout):
    with patch("leshy.rdp_tls_probe.socket.create_connection") as connect:
        with pytest.raises(ValueError, match="Invalid RDP timeout"):
            probe_rdp_tls("127.0.0.1", "a" * 64, timeout=timeout)
        connect.assert_not_called()


@pytest.mark.parametrize("timeout", [0.0, 0.099, 15.001, True])
def test_invalid_timeout_never_connects(timeout):
    with patch("leshy.rdp_tls_probe.socket.create_connection") as connect:
        with pytest.raises(ValueError, match="Invalid RDP timeout"):
            probe_rdp_tls("127.0.0.1", "a" * 64, timeout=timeout)
        connect.assert_not_called()
