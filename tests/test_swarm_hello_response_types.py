"""Offline regressions: a malformed public seed must not alter peer state."""
import json
from unittest.mock import Mock, patch

import pytest

from leshy.swarm_membership import SwarmMembership


def _membership():
    member = SwarmMembership.__new__(SwarmMembership)
    member._endpoint = "https://seed.example.org/v1/swarm/hello"
    member._identity = Mock()
    member._identity.sign.return_value = {"signature": "test"}
    member._identity.key_id = "own-key"
    member._advertise_url = ""
    member._allow_local_http = False
    member._peer_cache = Mock()
    return member


@pytest.mark.parametrize("payload", [None, [], "accepted", 42, True])
def test_hello_rejects_non_object_response_without_learning_peers(payload):
    member = _membership()
    with patch("leshy.swarm_membership.post_seed_hello",
               return_value=json.dumps(payload).encode("utf-8")):
        with pytest.raises(ValueError, match="формат bootstrap-ответа"):
            member._hello()
    member._peer_cache.learn.assert_not_called()


@pytest.mark.parametrize("peer_count", [True, False, -1, "3", 1.5])
def test_hello_rejects_invalid_peer_count_without_learning_peers(peer_count):
    member = _membership()
    payload = {"accepted": True, "peer_count": peer_count, "peers": []}
    with patch("leshy.swarm_membership.post_seed_hello",
               return_value=json.dumps(payload).encode("utf-8")):
        with pytest.raises(ValueError, match="число участников"):
            member._hello()
    member._peer_cache.learn.assert_not_called()
