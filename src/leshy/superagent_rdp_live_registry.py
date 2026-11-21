"""Private in-process RDP session control registry for SuperAgent Web UI.

No Web request creates an RDP connection. The trusted SuperAgent factory alone
registers an already owner-scoped read-only session. Status exposes no PNG,
credentials, remote protocol data or executable action receipts.
"""
from __future__ import annotations

import hmac
import threading
from typing import Any

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor


class RDPLiveRegistry:
    def __init__(self, service: Any, *, max_sessions: int = 4) -> None:
        if type(max_sessions) is not int or not 1 <= max_sessions <= 16:
            raise ValueError("Invalid native RDP control-plane capacity")
        self._service = service
        self._lock = threading.RLock()
        self._sessions: dict[tuple[str, str], tuple[str, Any]] = {}
        self._limit = max_sessions

    def _verify_owner(self, actor: VerifiedActor, ticket_id: str) -> None:
        if not isinstance(actor, VerifiedActor):
            raise PermissionError("Verified Web actor required")
        identity = getattr(getattr(self._service, "app", None), "owner_identity", None)
        if identity is None:
            raise PermissionError("Verified owner identity unavailable")
        RemotePermissionPolicy(identity)._check_actor(actor)
        if (type(ticket_id) is not str or not ticket_id.startswith("SA-")
                or len(ticket_id) > 100):
            raise ValueError("Invalid SuperAgent task ID")
        # The service's SQLite ticket lookup is the authority for ownership.
        self._service.get(ticket_id, owner_id=actor.owner_id)

    def register(self, actor: VerifiedActor, ticket_id: str, session: Any) -> None:
        """Called only by the trusted in-process factory; never by HTTP."""
        self._verify_owner(actor, ticket_id)
        if (getattr(session, "_actor", None) != actor
                or getattr(session, "_ticket_id", None) != ticket_id):
            raise PermissionError("RDP session owner or ticket mismatch")
        key = (actor.owner_id, ticket_id)
        with self._lock:
            if key in self._sessions or len(self._sessions) >= self._limit:
                raise RuntimeError("One live RDP view per task; capacity reached")
            # Serialize registration with Stop of this exact ticket.
            session.reauthorize()
            self._sessions[key] = (actor.session_fingerprint, session)

    def _lookup(self, actor: VerifiedActor, ticket_id: str) -> Any | None:
        key = (actor.owner_id, ticket_id)
        row = self._sessions.get(key)
        if row is None:
            return None
        expected_cookie, session = row
        if not hmac.compare_digest(expected_cookie, actor.session_fingerprint):
            raise PermissionError("RDP view belongs to another verified Web session")
        return session

    @staticmethod
    def _disconnected() -> dict[str, Any]:
        return {
            "connected": False, "graphics_active": False,
            "screen_available": False, "input_enabled": False,
            "reasoning_available": False, "proposal": None,
            "e2e_verified": False, "status": "no_live_session",
        }

    def status(self, actor: VerifiedActor, ticket_id: str) -> dict[str, Any]:
        self._verify_owner(actor, ticket_id)
        with self._lock:
            session = self._lookup(actor, ticket_id)
        if session is None:
            return self._disconnected()
        try:
            session.reauthorize()
            snapshot = session.snapshot()
            proposal = session.last_proposal()
            session.reauthorize()
        except (PermissionError, ValueError, KeyError):
            self.stop_ticket(actor.owner_id, ticket_id)
            return self._disconnected()
        view: dict[str, Any] = {
            "connected": True,
            "graphics_active": snapshot["graphics_active"] is True,
            "screen_available": False,  # No verified frame transfer to Web.
            "input_enabled": False,
            "e2e_verified": False,
            "reasoning_available": bool(proposal),
            "status": "graphics_receiving" if snapshot["graphics_active"] else "waiting_for_native_transport",
            "node_id": snapshot["node_id"],
            "task_id": snapshot["ticket_id"],
            "stopped": bool(snapshot["stopped"]),
            "proposal": None,
        }
        if proposal is not None:
            view["proposal"] = {
                "kind": proposal.kind,
                "reason": proposal.reason[:600],
                "question": proposal.question[:300],
                "x": proposal.x,
                "y": proposal.y,
                "frame_update": proposal.update_count,
                "execution_status": "not_executed",
                "owner_approval_issued": False,
            }
        return view

    def stop(self, actor: VerifiedActor, ticket_id: str) -> dict[str, Any]:
        """Web Stop needs a live verified owner, but not a still-valid RDP grant.

        Stop must still work if the remote permission was just revoked.
        """
        self._verify_owner(actor, ticket_id)
        with self._lock:
            session = self._lookup(actor, ticket_id)
            if session is None:
                return {"stopped": False, "status": "no_live_session"}
            del self._sessions[(actor.owner_id, ticket_id)]
        session.stop()
        return {"stopped": True, "status": "stopped"}

    def stop_ticket(self, owner_id: str, ticket_id: str) -> None:
        """Internal ticket Stop, not a public owner-id based API."""
        with self._lock:
            row = self._sessions.pop((owner_id, ticket_id), None)
        if row is not None:
            row[1].stop()

    def stop_all(self) -> None:
        with self._lock:
            items = list(self._sessions.values())
            self._sessions.clear()
        for _, session in items:
            session.stop()


__all__ = ["RDPLiveRegistry"]
