"""Preflight must be owner-authorized before opening a network socket."""
from __future__ import annotations

import pytest
import leshy.superagent_rdp_tls as guard


class Actor:
    owner_id = "alice"


def test_rejects_before_tcp_when_rdp_grant_absent(monkeypatch):
    events = []

    def deny(policy, actor, node, capability):
        events.append(("auth", capability))
        raise PermissionError("Access denied")

    monkeypatch.setattr(guard, "_node_ip", lambda value: "192.0.2.10")
    monkeypatch.setattr(guard, "_registered_pin", deny)
    monkeypatch.setattr(guard, "probe_rdp_tls", lambda *a, **kw: pytest.fail("outbound connection forbidden"))
    with pytest.raises(PermissionError):
        guard.verify_authorized_rdp_tls(object(), Actor(), "192.0.2.10", "a" * 64)
    assert events == [("auth", "rdp")]


def test_rechecks_access_after_tls_handshake(monkeypatch):
    order = []
    result = object()

    def verify(policy, actor, node, capability):
        order.append(("auth", capability))
        return "verified-ssh-registration"

    def probe(node, pin, **kwargs):
        order.append(("network", node))
        return result

    monkeypatch.setattr(guard, "_node_ip", lambda value: "192.0.2.10")
    monkeypatch.setattr(guard, "_registered_pin", verify)
    monkeypatch.setattr(guard, "probe_rdp_tls", probe)
    assert guard.verify_authorized_rdp_tls(
        object(), Actor(), "192.0.2.10", "a" * 64
    ) is result
    assert order == [
        ("auth", "rdp"), ("network", "192.0.2.10"), ("auth", "rdp")
    ]


def test_revocation_during_tls_handshake_discards_result(monkeypatch):
    calls = [0]

    def revoke(policy, actor, node, capability):
        calls[0] += 1
        if calls[0] == 2:
            raise PermissionError("Revoked")
        return "verified-ssh-registration"

    monkeypatch.setattr(guard, "_node_ip", lambda value: "192.0.2.10")
    monkeypatch.setattr(guard, "_registered_pin", revoke)
    monkeypatch.setattr(guard, "probe_rdp_tls", lambda *a, **kw: object())
    with pytest.raises(PermissionError, match="Revoked"):
        guard.verify_authorized_rdp_tls(
            object(), Actor(), "192.0.2.10", "a" * 64
        )
    assert calls == [2]
