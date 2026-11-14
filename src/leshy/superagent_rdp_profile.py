"""Safe native RDP connection profile for an enrolled owner-controlled node.

This is a *client launcher profile*, not an autonomous desktop executor.
A registered SSH host fingerprint does NOT establish an RDP TLS identity.
The native RDP client negotiates its own server certificate and credentials.
Never place secrets, arbitrary arguments or user-controlled paths in a .rdp.
"""
from __future__ import annotations

import ipaddress
from typing import Any

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_verified_ssh import _node_ip
from .superagent_ssh_terminal import _registered_pin


def rdp_connection_profile(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    node_id: str,
) -> dict[str, Any]:
    """Owner-only, non-secret file content for Microsoft's native RDP client."""
    node = _node_ip(node_id)
    address = ipaddress.ip_address(node)
    if address.version != 4:
        raise ValueError("RDP-профиль пока поддерживает только IPv4")
    policy._check_actor(actor)
    with policy.storage.connection() as db:
        registered = db.execute(
            "SELECT owner_id FROM superagent_remote_nodes WHERE node_id=?",
            (node,),
        ).fetchone()
    if registered is None:
        raise PermissionError("Сервер предварительно должен быть зарегистрирован владельцем")
    # This checks the verified owner's existing registration/rdp grant,
    # not the *live* identity of the RDP server. Windows must verify its
    # certificate independently before any password is entered.
    _registered_pin(policy, actor, node, "rdp")
    lines = [
        "screen mode id:i:2",
        "use multimon:i:0",
        "desktopwidth:i:1280",
        "desktopheight:i:800",
        "session bpp:i:32",
        "full address:s:" + node + ":3389",
        "prompt for credentials:i:1",
        "enablecredsspsupport:i:1",
        "negotiate security layer:i:1",
        "authentication level:i:2",
        "gatewayusagemethod:i:0",
        "redirectclipboard:i:0",
        "drivestoredirect:s:",
        "redirectdrives:i:0",
        "redirectprinters:i:0",
        "redirectcomports:i:0",
        "redirectsmartcards:i:0",
        "audiomode:i:2",
    ]
    return {
        "node_id": node,
        "filename": "Leshy-RDP-" + node.replace(".", "-") + ".rdp",
        "rdp": "\r\n".join(lines) + "\r\n",
        "requires_local_client": True,
        "requires_user_login": True,
        "certificate_verification": "native_rdp_client",
        "remote_desktop_control_by_agent": False,
    }


__all__ = ["rdp_connection_profile"]
