"""Offline regression coverage for public swarm failover; no sockets or workers."""

from types import SimpleNamespace

from leshy.swarm_membership import SwarmMembership


def _membership(*, seeds=(), endpoint="", peers=(), manual=False):
    membership = object.__new__(SwarmMembership)
    membership._manual_configured = manual
    membership._endpoint = endpoint
    membership._seed_urls = tuple(seeds)
    membership._peer_urls = tuple(peers)
    membership._peer_cache = SimpleNamespace(urls=lambda: tuple(peers))
    return membership


def test_rotates_between_known_public_seeds_without_network():
    first = "https://first.example.org/v1/swarm/hello"
    second = "https://second.example.org/v1/swarm/hello"
    membership = _membership(seeds=(first, second), endpoint=first)
    assert membership._try_next_seed() is True
    assert membership._endpoint == second
    assert membership._try_next_seed() is True
    assert membership._endpoint == first


def test_single_seed_cannot_report_successful_failover():
    only = "https://only.example.org/v1/swarm/hello"
    membership = _membership(seeds=(only,), endpoint=only)
    assert membership._try_next_seed() is False
    assert membership._endpoint == only


def test_manual_endpoint_must_not_be_replaced_by_catalog():
    manual = "http://localhost:8765/v1/swarm/hello"
    membership = _membership(
        seeds=("https://first.example.org/v1/swarm/hello",),
        endpoint=manual,
        manual=True,
    )
    assert membership._try_next_seed() is False
    assert membership._endpoint == manual


def test_invalid_peer_hint_does_not_block_valid_catalog_failover():
    first = "https://first.example.org/v1/swarm/hello"
    second = "https://second.example.org/v1/swarm/hello"
    membership = _membership(
        seeds=(first, second), endpoint=first,
        peers=("https://127.0.0.1", "http://metadata.example.org"),
    )
    assert membership._try_next_seed() is True
    assert membership._endpoint == second


def test_valid_peer_hint_is_fallback_after_catalog_seed():
    first = "https://first.example.org/v1/swarm/hello"
    peer = "https://peer.example.org/v1/swarm/hello"
    membership = _membership(seeds=(first,), endpoint=first, peers=(peer,))
    assert membership._try_next_seed() is True
    assert membership._endpoint == peer
