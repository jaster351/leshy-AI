import pytest
from leshy.ip_block_diagnostics import diagnose_ip


def test_reject_private_ip():
    with pytest.raises(ValueError):
        diagnose_ip("127.0.0.1")


def test_reject_hostname():
    with pytest.raises(ValueError):
        diagnose_ip("example.com")
