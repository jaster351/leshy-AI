"""Offline regression: manual and peer bootstrap URLs use catalog restrictions."""
import pytest

from leshy.swarm_membership import bootstrap_endpoint


@pytest.mark.parametrize("url", [
    "https://127.0.0.1",
    "https://10.1.2.3",
    "https://169.254.169.254",
    "https://localhost",
    "https://node.internal",
    "https://node.local",
    "http://seed.example.org",
    "https://user:pass@seed.example.org",
    "https://seed.example.org/v1/swarm/hello",
    "https://seed.example.org/?token=secret",
])
def test_manual_seed_rejects_disallowed_catalog_targets(url):
    with pytest.raises(ValueError):
        bootstrap_endpoint(url)


def test_manual_seed_normalizes_public_origin():
    assert bootstrap_endpoint("https://seed.example.org/v1/swarm") == (
        "https://seed.example.org/v1/swarm/hello"
    )


def test_loopback_pilot_requires_explicit_opt_in():
    with pytest.raises(ValueError):
        bootstrap_endpoint("http://localhost:8765")
    assert bootstrap_endpoint("http://localhost:8765", allow_local_http=True) == (
        "http://localhost:8765/v1/swarm/hello"
    )
