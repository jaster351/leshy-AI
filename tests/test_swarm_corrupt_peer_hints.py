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
