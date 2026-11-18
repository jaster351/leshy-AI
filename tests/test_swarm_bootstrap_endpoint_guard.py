"""Regression coverage for public swarm bootstrap URL validation.

Static addition only: do not run tests without the owner's approval.
"""
import pytest

from leshy.swarm_membership import bootstrap_endpoint


@pytest.mark.parametrize("url", [
    "https://127.0.0.1",
    "https://10.0.0.1",
    "https://169.254.169.254",
    "https://[::1]",
    "https://[fc00::1]",
    "https://localhost",
    "https://node.local",
    "https://node.internal",
    "https://user:password@node.example.com",
    "https://node.example.com/v1/swarm?token=secret",
    "https://node.example.com/other",
])
def test_public_bootstrap_rejects_private_or_unsafe_url(url):
    with pytest.raises(ValueError):
        bootstrap_endpoint(url)


def test_public_bootstrap_keeps_approved_origin_and_path():
    assert bootstrap_endpoint("https://node.example.com/v1/swarm") == (
        "https://node.example.com/v1/swarm/hello"
    )


def test_local_pilot_requires_explicit_opt_in():
    with pytest.raises(ValueError):
        bootstrap_endpoint("http://127.0.0.1:8080")
    assert bootstrap_endpoint("http://127.0.0.1:8080", allow_local_http=True) == (
        "http://127.0.0.1:8080/v1/swarm/hello"
    )
