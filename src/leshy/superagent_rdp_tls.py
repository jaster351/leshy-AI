"""Owner-scoped entry point for the credential-free RDP TLS handshake.

The existing per-node owner/provider authorization is required *before*
opening the network socket and rechecked after the TLS handshake. This is
not an interactive RDP client, login or UI/LLM route.
"""
from __future__ import annotations

from .rdp_tls_probe import RDPTLSProbeResult, probe_rdp_tls
from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_ssh_terminal import _registered_pin
from .superagent_verified_ssh import _node_ip


def verify_authorized_rdp_tls(
    policy: RemotePermissionPolicy,
    actor: VerifiedActor,
    node_id: str,
    expected_rdp_tls_sha256: str,
    *,
    timeout: float = 5.0,
) -> RDPTLSProbeResult:
    """Verify a *separate* owner-enrolled RDP TLS fingerprint.

    The SSH host fingerprint stored in the node registry is for access
    control to an enrolled node only. It is NOT the expected RDP TLS
    fingerprint and is never used as a substitute for one.
    """
    node = _node_ip(node_id)
    _registered_pin(policy, actor, node, "rdp")
    result = probe_rdp_tls(
        node, expected_rdp_tls_sha256, timeout=timeout
    )
    # A user's Web-session or per-node grant may expire during a TCP/TLS
    # handshake. Do not return a usable success indicator after revocation.
    _registered_pin(policy, actor, node, "rdp")
    return result


__all__ = ["verify_authorized_rdp_tls"]
