"""Startup must tolerate a damaged cached swarm peer collection."""

from types import SimpleNamespace

import pytest

from leshy.swarm_membership import SwarmMembership


class _Directory:
    def __init__(self, *_args, **_kwargs):
        self.source = "cache"

    def cached(self):
        return ("https://seed.example.net/v1/swarm/hello",)


@pytest.mark.parametrize("broken", [ValueError("bad"), OSError("unreadable"), None, {"bad": "shape"}])
def test_startup_with_bad_peer_cache_still_uses_catalog(monkeypatch, tmp_path, broken):
    monkeypatch.delenv("LESHY_SWARM_BOOTSTRAP_URL", raising=False)
    monkeypatch.delenv("LESHY_SWARM_PUBLIC_ENDPOINT", raising=False)
    monkeypatch.setattr("leshy.swarm_membership.GitHubBootstrapDirectory", _Directory)
    monkeypatch.setattr("leshy.swarm_membership.problem", lambda *args: None)

    class _Cache:
        def __init__(self, *_args):
            pass

        def load(self):
            if isinstance(broken, Exception):
                raise broken
            return broken

    monkeypatch.setattr("leshy.swarm_membership.SwarmPeerCache", _Cache)
    app = SimpleNamespace(settings=SimpleNamespace(brain_dir=tmp_path))
    membership = SwarmMembership(app)
    assert membership._peer_urls == ()
    assert membership._endpoint == "https://seed.example.net/v1/swarm/hello"
    assert membership._seed_urls == ("https://seed.example.net/v1/swarm/hello",)
