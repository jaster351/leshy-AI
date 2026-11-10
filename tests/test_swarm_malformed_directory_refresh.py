"""Offline regression for malformed public swarm directory refreshes."""

from types import SimpleNamespace

import pytest

from leshy.swarm_membership import SwarmMembership


@pytest.mark.parametrize("payload", [None, "invalid", 42, {"seeds": []}])
def test_malformed_refresh_preserves_verified_seeds(monkeypatch, payload):
    first = "https://first.example.org/v1/swarm/hello"
    member = object.__new__(SwarmMembership)
    member._manual_configured = False
    member._last_directory_refresh = 0.0
    member._seed_urls = (first,)
    member._seed_index = 0
    member._endpoint = first
    member._allow_local_http = False
    member._last_error = ""
    member._directory = SimpleNamespace(refresh=lambda: payload, last_error="", source="github")
    failures = []
    monkeypatch.setattr("leshy.swarm_membership.problem", lambda *args: failures.append(args))

    member._discover_seeds()

    assert member._seed_urls == (first,)
    assert member._endpoint == first
    assert member._last_error == "invalid_directory_payload"
    assert failures


def test_explicit_empty_directory_still_revokes_seeds(monkeypatch):
    first = "https://first.example.org/v1/swarm/hello"
    member = object.__new__(SwarmMembership)
    member._manual_configured = False
    member._last_directory_refresh = 0.0
    member._seed_urls = (first,)
    member._seed_index = 0
    member._endpoint = first
    member._allow_local_http = False
    member._directory = SimpleNamespace(refresh=lambda: [], last_error="", source="github")
    monkeypatch.setattr("leshy.swarm_membership.event", lambda *args, **kwargs: None)

    member._discover_seeds()

    assert member._seed_urls == ()
    assert member._endpoint == ""
