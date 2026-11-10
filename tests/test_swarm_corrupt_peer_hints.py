"""Invalid cached peer hints must not terminate the membership worker."""

from leshy.swarm_membership import SwarmMembership


class _PeerCache:
    def urls(self):
        return (
            "https://example.org/v1/swarm/hello",
            "http://127.0.0.1:9999/v1/swarm/hello",
            "https://example.org/v1/swarm/hello?bad=1",
            "not a url",
        )


def test_invalid_cached_peer_hints_are_skipped():
    membership = object.__new__(SwarmMembership)
    membership._seed_urls = ("https://seed.example.net/v1/swarm/hello",)
    membership._peer_cache = _PeerCache()
    hops = membership._candidate_hops()
    assert hops == (
        "https://seed.example.net/v1/swarm/hello",
        "https://example.org/v1/swarm/hello",
    )


def test_failover_skips_invalid_cached_peer_hints():
    membership = object.__new__(SwarmMembership)
    membership._manual_configured = False
    membership._seed_urls = ("https://seed.example.net/v1/swarm/hello",)
    membership._peer_cache = _PeerCache()
    membership._endpoint = "https://seed.example.net/v1/swarm/hello"
    assert membership._try_next_seed() is True
    assert membership._endpoint == "https://example.org/v1/swarm/hello"


def test_corrupt_catalog_seeds_are_isolated():
    membership = object.__new__(SwarmMembership)
    membership._allow_local_http = False
    result = membership._validated_seeds((
        "https://seed.example.net/v1/swarm",
        "http://127.0.0.1:8000/v1/swarm",
        "https://seed.example.net/v1/swarm",
        "https://seed.example.net/v1/swarm?invalid=1",
        "not a url",
    ))
    assert result == ("https://seed.example.net/v1/swarm/hello",)


def test_empty_and_invalid_catalog_seeds_do_not_raise():
    membership = object.__new__(SwarmMembership)
    membership._allow_local_http = False
    assert membership._validated_seeds(("", "not a url", "http://127.0.0.1/")) == ()


def test_malformed_directory_collection_is_ignored():
    membership = object.__new__(SwarmMembership)
    membership._allow_local_http = False
    for invalid in (None, "https://seed.example.net", 42, {"seeds": []}):
        assert membership._validated_seeds(invalid) == ()


class _BrokenPeerCache:
    def urls(self):
        raise ValueError("corrupt peer cache")


class _BrokenDirectory:
    last_error = ""

    def refresh(self):
        raise OSError("offline directory")


def test_peer_cache_exception_keeps_valid_catalog_seeds(monkeypatch):
    monkeypatch.setattr("leshy.swarm_membership.problem", lambda *args: None)
    membership = object.__new__(SwarmMembership)
    membership._seed_urls = ("https://seed.example.net/v1/swarm/hello",)
    membership._peer_cache = _BrokenPeerCache()
    assert membership._candidate_hops() == membership._seed_urls
    assert membership._peer_urls == ()


def test_peer_cache_malformed_collection_keeps_catalog_seeds():
    membership = object.__new__(SwarmMembership)
    membership._seed_urls = ("https://seed.example.net/v1/swarm/hello",)
    membership._peer_cache = type("Cache", (), {"urls": lambda self: None})()
    assert membership._candidate_hops() == membership._seed_urls


def test_directory_refresh_exception_keeps_previous_seeds(monkeypatch):
    monkeypatch.setattr("leshy.swarm_membership.problem", lambda *args: None)
    membership = object.__new__(SwarmMembership)
    membership._manual_configured = False
    membership._last_directory_refresh = 0.0
    membership._directory = _BrokenDirectory()
    membership._seed_urls = ("https://seed.example.net/v1/swarm/hello",)
    membership._endpoint = membership._seed_urls[0]
    membership._last_error = ""
    membership._discover_seeds()
    assert membership._seed_urls == ("https://seed.example.net/v1/swarm/hello",)
    assert membership._endpoint == membership._seed_urls[0]
    assert membership._last_error == "OSError"
