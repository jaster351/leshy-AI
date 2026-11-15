"""Private SuperAgent remote-access inventory and explicit grant revocation.

This is not an enrollment, SSH or RDP endpoint. Never return remote credentials,
raw SSH output, host keys, or another owner's machines. Server-verified provider
identity and a live Web session are mandatory even for listing.
"""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor


def authorized_remote_context(app: Any, owner_id: str, raw_cookie: str):
    """Derive actor from the *real* HttpOnly Web cookie, not caller JSON."""
    identity = getattr(app, "owner_identity", None)
    if identity is None:
        raise PermissionError("Для управления компьютерами нужен вход через провайдера")
    policy = RemotePermissionPolicy(identity)
    actor = policy.actor_from_web_cookie(raw_cookie)
    if actor.owner_id != owner_id:
        raise PermissionError("Владелец Web-сессии не совпадает с владельцем раздела")
    policy._check_actor(actor)
    return policy, actor


def list_remote_access(policy: RemotePermissionPolicy, actor: VerifiedActor,
                       *, remote_admin_enabled: bool) -> dict[str, Any]:
    """Show only existing enrollment and unexpired grants owned by this actor.

    Does not contact computers or claim that a saved host key is still valid.
    """
    policy._check_actor(actor)
    now = time.time()
    with policy.storage.connection() as conn:
        nodes = conn.execute(
            "SELECT node_id,created_at FROM superagent_remote_nodes "
            "WHERE owner_id=? ORDER BY created_at DESC LIMIT 65",
            (actor.owner_id,),
        ).fetchall()
        grants = conn.execute(
            "SELECT g.node_id,g.grantee_id,g.capability,g.expires_at "
            "FROM superagent_remote_permissions AS g "
            "JOIN superagent_remote_nodes AS n ON n.node_id=g.node_id "
            "WHERE n.owner_id=? AND g.expires_at>? "
            "ORDER BY g.expires_at ASC LIMIT 201",
            (actor.owner_id, now),
        ).fetchall()
    # The response is deliberately capped to prevent oversized UI data.
    visible_nodes = [{
        "node_id": str(row["node_id"])[:128],
        "registered_at": datetime.fromtimestamp(
            float(row["created_at"]), tz=timezone.utc
        ).isoformat(),
        "status": "registered_not_connected",
    } for row in nodes[:64]]
    visible_grants = [{
        "node_id": str(row["node_id"])[:128],
        "grantee_id": str(row["grantee_id"])[:200],
        "capability": str(row["capability"])[:20],
        "expires_at": datetime.fromtimestamp(
            float(row["expires_at"]), tz=timezone.utc
        ).isoformat(),
    } for row in grants[:200]]
    return {
        "provider_verified": True,
        "remote_admin_enabled": bool(remote_admin_enabled),
        "nodes": visible_nodes,
        "active_grants": visible_grants,
        "nodes_truncated": len(nodes) > 64,
        "grants_truncated": len(grants) > 200,
        "transport_ready": False,  # No SSH/RDP transport enabled by this route.
    }


def revoke_remote_grant(policy: RemotePermissionPolicy, actor: VerifiedActor,
                        payload: dict[str, Any]) -> dict[str, Any]:
    """Only node owner may revoke one exact existing capability.

    No grants, registrations, remote connections or OS effects are possible.
    """
    if not isinstance(payload, dict) or set(payload) != {
        "node_id", "grantee_id", "capability"
    }:
        raise ValueError("Выберите одно разрешение для отзыва")
    node = payload["node_id"]
    grantee = payload["grantee_id"]
    capability = payload["capability"]
    if not all(isinstance(x, str) and x and len(x) <= maxlen for x, maxlen in (
        (node, 128), (grantee, 200), (capability, 20)
    )):
        raise ValueError("Некорректные параметры разрешения")
    # The policy rechecks provider login, live session and node ownership.
    policy.revoke(actor, node, grantee, capability)
    return {"revoked": True}


__all__ = ["authorized_remote_context", "list_remote_access", "revoke_remote_grant"]
