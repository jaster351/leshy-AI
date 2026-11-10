"""Offline regression checks for scheduled swarm catalog revocation (no network)."""

from types import SimpleNamespace

from leshy.swarm_membership import SwarmMembership


FIRST = "https://first.example.org/v1/swarm/hello"
SECOND = "https://second.example.org/v1/swarm/hello"
PEER = "https://peer.example.org/v1/swarm/hello"


def _membership(*, response, seeds=(FIRST,), endpoint=FIRST, error=""):
    member = object.__new__(SwarmMembership)
    member._manual_configured = False
    member._allow_local_http = False
    member._last_directory_refresh = 0.0
    member._last_error = ""
    member._seed_urls = tuple(seeds)
    member._seed_index = 0
    member._endpoint = endpoint
    member._catalog_revoked = False
    member._peer_urls = (PEER,)
    member._peer_cache = SimpleNamespace(urls=lambda: (PEER,))
    member._directory = SimpleNamespace(
        refresh=lambda: response, last_error=error, source="github_catalog",
    )
    return member


def test_successful_refresh_keeps_still_authorized_endpoint():
    member = _membership(response=[SECOND, FIRST])
    member._discover_seeds()
    assert member._endpoint == FIRST
    assert member._seed_urls == (SECOND, FIRST)


def test_removed_healthy_endpoint_is_replaced_on_refresh():
    member = _membership(response=[SECOND])
    member._discover_seeds()
    assert member._endpoint == SECOND


def test_explicit_empty_catalog_revokes_seed_and_peer_hints():
    member = _membership(response=[])
    member._discover_seeds()
    assert member._catalog_revoked is True
    assert member._endpoint == ""
    assert member._candidate_hops() == ()


def test_invalid_catalog_does_not_revoke_known_seed():
    member = _membership(response={"unexpected": "shape"})
    member._discover_seeds()
    assert member._endpoint == FIRST
    assert member._seed_urls == (FIRST,)
    assert member._catalog_revoked is False


def test_failed_refresh_keeps_known_seed():
    member = _membership(response=[], error="offline")
    member._discover_seeds()
    assert member._endpoint == FIRST
    assert member._seed_urls == (FIRST,)


def test_catalog_restoration_reenables_discovery_after_revocation():
    member = _membership(response=[])
    member._discover_seeds()
    member._directory.refresh = lambda: [SECOND]
    member._last_directory_refresh = 0.0
    member._discover_seeds()
    assert member._catalog_revoked is False
    assert member._endpoint == SECOND
