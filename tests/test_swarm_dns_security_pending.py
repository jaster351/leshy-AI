"""Pending security regressions for public swarm bootstrap DNS resolution.

These are deliberately xfail until the HTTPS transport pins the validated
destination address. URL validation alone cannot prevent DNS rebinding.
No network calls: resolver results are synthetic.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from leshy.swarm_membership import bootstrap_endpoint


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.20.30.40",),
        ("169.254.169.254",),
        ("::1",),
        ("fd00::1",),
        ("8.8.8.8", "192.168.1.5"),
    ],
)
@pytest.mark.xfail(strict=True, reason="Issue #132: public DNS must be validated and pinned at connect time")
def test_public_seed_rejects_private_or_mixed_dns_answers(addresses):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "seed.example.org"
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET,
             socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
             (ip, port, 0, 0) if ":" in ip else (ip, port))
            for ip in addresses
        ]

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        with pytest.raises(ValueError):
            bootstrap_endpoint("https://seed.example.org")


@pytest.mark.xfail(strict=True, reason="Issue #132: transport must pin DNS answer and prevent rebinding")
def test_public_seed_must_not_re_resolve_unchecked_destination():
    calls = []

    def changing_dns(host, port, *args, **kwargs):
        calls.append(host)
        ip = "8.8.8.8" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    with patch("socket.getaddrinfo", side_effect=changing_dns):
        # The URL parser currently never resolves the hostname. A future
        # transport-level guard must resolve and pin the approved address.
        bootstrap_endpoint("https://seed.example.org")
        assert calls == ["seed.example.org"]


def test_local_pilot_requires_explicit_opt_in():
    with pytest.raises(ValueError):
        bootstrap_endpoint("http://127.0.0.1:8765")
    assert bootstrap_endpoint(
        "http://127.0.0.1:8765", allow_local_http=True
    ) == "http://127.0.0.1:8765/v1/swarm/hello"
