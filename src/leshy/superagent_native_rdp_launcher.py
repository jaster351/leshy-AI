"""Genuine owner-approved native RDP launch, not a simulated desktop.

INTERNAL ONLY: caller supplies a real authenticated SuperAgent owner and
separate scoped credential-delegation approval. No Web/LLM invocation, auto
retries, persisted secrets, or remote mouse/keyboard output.
"""
from __future__ import annotations

import math
import re
import threading
from typing import Any, Callable

from .rdp_credssp_channel import (
    PinnedRDPCredSSPChannel, connect_verified_rdp_credssp,
)
from .superagent_rdp_credentials import load_owner_rdp_credentials
from .native_spnego import _check_target
from .rdp_graphics_stream import GraphicsStreamPolicy
from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor
from .superagent_ssh_terminal import _registered_pin
from .superagent_verified_ssh import _node_ip

_PIN = re.compile(r"[0-9a-fA-F]{64}\Z")
_FILE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_. -]{0,254}\Z")


def start_owner_authorized_native_rdp(
    service: Any, *, ticket_id: str, actor: VerifiedActor,
    node_id: str, service_dns_name: str, enrolled_rdp_tls_sha256: str,
    credential_file: str,
    authorize_delegation: Callable[[str, str], None],
    timeout: float = 30.0,
    graphics_policy: GraphicsStreamPolicy | None = None,
    max_png_frames: int = 256,
) -> dict[str, Any]:
    """Run one pinned TLS+NLA+MCS+read-only graphics attempt on a worker.

    The trusted caller MUST independently verify a current, narrowly scoped
    owner approval to delegate credentials to this exact host and TLS pin.
    authorize_delegation is the server-side approval recheck, NEVER LLM text.
    It is checked before secret reads and again inside the native channel.
    No automatic retry after a possible password send.
    """
    if not isinstance(actor, VerifiedActor):
        raise PermissionError("Provider-verified actor required")
    if not callable(authorize_delegation):
        raise PermissionError("Independent RDP credential approval required")
    if type(ticket_id) is not str or not ticket_id.startswith("SA-") or len(ticket_id) > 100:
        raise ValueError("Invalid owner-scoped SuperAgent ticket")
    node = _node_ip(node_id)
    dns = _check_target(service_dns_name)
    if (type(enrolled_rdp_tls_sha256) is not str
            or not _PIN.fullmatch(enrolled_rdp_tls_sha256)):
        raise PermissionError("Independently enrolled RDP TLS pin required")
    if (type(credential_file) is not str or not _FILE.fullmatch(credential_file)
            or credential_file in {".", ".."} or not credential_file.lower().endswith(".json")):
        raise ValueError("Selected owner-local JSON credential filename required")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 30:
        raise ValueError("Invalid bounded RDP handshake timeout")
    if type(max_png_frames) is not int or not 1 <= max_png_frames <= 256:
        raise ValueError("Invalid PNG frame budget")
    if graphics_policy is not None and not isinstance(graphics_policy, GraphicsStreamPolicy):
        raise TypeError("Invalid RDP graphics policy")
    if not hasattr(service, "storage") or not hasattr(service, "create_rdp_cognitive_session"):
        raise TypeError("Trusted SuperAgent service required")
    identity = getattr(getattr(service, "app", None), "owner_identity", None)
    if identity is None:
        raise PermissionError("Verified owner identity unavailable")

    # Registration fingerprint is NOT the independently pinned RDP TLS cert.
    policy = RemotePermissionPolicy(identity)
    registered_pin = _registered_pin(policy, actor, node, "rdp")
    policy.require(actor, node, "rdp", observed_fingerprint=registered_pin)
    service.get(ticket_id, owner_id=actor.owner_id)
    session = service.create_rdp_cognitive_session(
        ticket_id, actor=actor, node_id=node,
        observed_fingerprint=registered_pin,
    )
    try:
        session.reauthorize()
    except BaseException:
        service._rdp_controls().stop_ticket(actor.owner_id, ticket_id)
        raise
    expected_pin = enrolled_rdp_tls_sha256.lower()

    def check_target(ip: str, name: str) -> None:
        if ip != node or name != dns:
            raise PermissionError("RDP server target identity changed")
        session.reauthorize()

    def approve(ip: str, name: str) -> None:
        check_target(ip, name)
        authorize_delegation(ip, name)
        check_target(ip, name)

    def run() -> None:
        connection: PinnedRDPCredSSPChannel | None = None
        try:
            session.set_native_phase("connecting")
            # Revoke a consumed confirmation BEFORE opening any TCP socket.
            # The same approval is checked again before reading secrets and
            # inside CredSSP immediately before the single credential send.
            approve(node, dns)
            connection = connect_verified_rdp_credssp(
                node, dns, expected_pin,
                authorize=check_target, timeout=timeout,
            )
            session.attach_native_handshake(connection)
            session.set_native_phase("tls_pinned")
            connection.authenticate_server()
            session.set_native_phase("credssp_bound")

            # Do not load secrets until independently approved server binding.
            approve(node, dns)
            credentials = load_owner_rdp_credentials(
                service.storage, actor.owner_id, node, credential_file
            )
            try:
                connection.delegate_password_credentials(
                    credentials, authorize_delegation=approve
                )
            finally:
                del credentials
            session.set_native_phase("credentials_sent")

            connection.exchange_mcs_basic_settings()
            session.set_native_phase("mcs_settings")
            connection.join_mcs_channels()
            session.set_native_phase("mcs_joined")
            local_ip = connection.local_client_address()
            connection.send_client_info(client_address=local_ip)
            session.set_native_phase("client_info")
            connection.receive_server_activation()
            session.set_native_phase("server_activated")
            connection.send_confirm_active()
            session.set_native_phase("confirm_active")
            connection.finalize_mcs_connection()
            session.set_native_phase("finalized")
            session.reauthorize()
            session.set_native_phase("graphics")
            session.receive_graphics(
                connection, policy=graphics_policy,
                max_png_frames=max_png_frames,
            )
            session.set_native_phase("stream_finished")
        except BaseException as exc:
            # No retry, no raw exception text, packets or secrets in status.
            session.native_failure(type(exc).__name__)
        finally:
            # An error while tearing down TLS must not skip bookkeeping,
            # replace the original wire error, or expose secret exception text.
            try:
                if connection is not None:
                    connection.close()
            except BaseException:
                session.native_failure("RDPTransportCloseError")
            finally:
                session.clear_native_handshake(connection)

    thread = threading.Thread(
        target=run, name="Leshy-RDP-native-" + ticket_id[:18],
        daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        service._rdp_controls().stop_ticket(actor.owner_id, ticket_id)
        raise
    return {
        "ticket_id": ticket_id, "node_id": node,
        "status": "starting", "credentials_delegated": False,
        "screen_available": False, "input_enabled": False,
        "windows_rdp_e2e_verified": False,
    }


__all__ = ["start_owner_authorized_native_rdp"]
