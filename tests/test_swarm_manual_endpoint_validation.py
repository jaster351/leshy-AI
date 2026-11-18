"""Regression coverage for public/manual swarm URL parity.

These tests intentionally do not perform network I/O or prove DNS rebinding
protection; that requires transport-level address pinning.
"""
import pytest

from leshy.swarm_membership import bootstrap_endpoint


@pytest.mark.parametrize("url", [
    "https://127.0.0.1", "https://10.0.0.2", "https://169.254.169.254",
    "https://[::1]", "https://[fc00::1]", "https://host.local",
    "https://host.internal", "https://localhost.localdomain",
    "https://user:password@example.org", "http://example.org",
    "https://example.org/?token=x", "https://example.org/other",
])
def test_manual_bootstrap_rejects_nonpublic_or_invalid_url(url):
    with pytest.raises(ValueError):
        bootstrap_endpoint(url)


def test_manual_bootstrap_normalizes_public_origin():
    assert bootstrap_endpoint("https://seed.example.org/v1/swarm") == (
        "https://seed.example.org/v1/swarm/hello"
    )


def test_manual_bootstrap_empty_remains_unconfigured():
    assert bootstrap_endpoint("") == ""


def test_local_pilot_requires_explicit_opt_in():
    with pytest.raises(ValueError):
        bootstrap_endpoint("http://127.0.0.1:8765")
    assert bootstrap_endpoint(
        "http://127.0.0.1:8765", allow_local_http=True,
    ) == "http://127.0.0.1:8765/v1/swarm/hello"
