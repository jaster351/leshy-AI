"""Private in-process RDP session control registry for SuperAgent Web UI.

No Web request creates an RDP connection. The trusted SuperAgent factory alone
registers an already owner-scoped read-only session. Status exposes no PNG,
credentials, remote protocol data or executable action receipts.
"""
from __future__ import annotations

import hmac
import threading
from typing import Any, Callable

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor


class RDPLiveRegistry:
    def __init__(self, service: Any, *, max_sessions: int = 4) -> None:
        if type(max_sessions) is not int or not 1 <= max_sessions <= 16:
            raise ValueError("Invalid native RDP control-plane capacity")
        self._service = service
        self._lock = threading.RLock()
        self._sessions: dict[tuple[str, str], tuple[str, Any]] = {}
        # A ticket cannot reopen until its preceding socket Stop finishes.
        # Failed Stop remains reserved, rather than creating overlapping
        # sessions with uncertain remote state.
        self._closing: set[tuple[str, str]] = set()
        self._failed_closes: set[tuple[str, str]] = set()
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
            if (key in self._sessions or key in self._closing
                    or len(self._sessions) + len(self._closing) >= self._limit):
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
            "registered": False, "connected": False, "graphics_active": False,
            "screen_available": False, "frame_update": 0,
            "input_enabled": False,
            "reasoning_available": False, "proposal": None,
            "e2e_verified": False, "status": "no_live_session",
        }

    def status(self, actor: VerifiedActor, ticket_id: str) -> dict[str, Any]:
        self._verify_owner(actor, ticket_id)
        with self._lock:
            session = self._lookup(actor, ticket_id)
            if session is None:
                key = (actor.owner_id, ticket_id)
                if key in self._closing:
                    failed = key in self._failed_closes
                    return {
                        **self._disconnected(),
                        "registered": True, "stopped": True,
                        "status": "stop_failed_locked" if failed else "stop_in_progress",
                        "native_phase": "stop_failed" if failed else "stopping",
                    }
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
        # A native protocol failure revokes connected/screen claims even
        # during the brief race before the graphics worker closes TLS.
        healthy_graphics = (
            snapshot["graphics_active"] is True
            and snapshot.get("native_phase") != "failed"
        )
        view: dict[str, Any] = {
            "registered": True,
            "connected": healthy_graphics,
            "graphics_active": healthy_graphics,
            "screen_available": (
                healthy_graphics
                and snapshot.get("frame_available") is True
            ),
            "frame_update": int(snapshot.get("frame_update", 0)),
            "input_enabled": False,
            "e2e_verified": False,
            "reasoning_available": bool(proposal),
            "status": (
                "native_failed" if snapshot.get("native_phase") == "failed"
                else "stream_finished" if snapshot.get("native_phase") == "stream_finished"
                else "graphics_receiving" if snapshot["graphics_active"]
                else "waiting_for_native_transport"
            ),
            "native_phase": snapshot.get("native_phase", "not_started"),
            "native_error_type": snapshot.get("native_error_type", ""),
            "cognitive_running": bool(snapshot.get("cognitive_running", False)),
            "cognitive_completed": int(snapshot.get("cognitive_completed", 0)),
            "cognitive_error_type": str(snapshot.get("cognitive_error_type", ""))[:80],
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

    def transmit_png(
        self, actor: VerifiedActor, ticket_id: str,
        deliver: Callable[[bytes, int], None],
    ) -> bool:
        """Serialize an actual PNG response with Stop of the same session.

        The Web handler must bound socket I/O by a short timeout. While the
        image is written Stop waits at this lock. Once Stop completes,
        no new response can begin. Never hold this lock for model inference.
        """
        if not callable(deliver):
            raise TypeError("Trusted binary Web writer is required")
        self._verify_owner(actor, ticket_id)
        with self._lock:
            session = self._lookup(actor, ticket_id)
            if session is None:
                return False
            session.reauthorize()
            image = session.current_native_png()
            if image is None:
                return False
            # Authorization must still hold at the exact output boundary.
            session.reauthorize()
            deliver(image.png, image.update_count)
            return True

    def _stop_reserved(self, key: tuple[str, str], session: Any) -> None:
        """Do not release same-task capacity until Stop actually finishes.

        On Stop exception, keep the reservation for safety. A new connection
        can start only after a process-level reset, not after uncertain Stop.
        """
        try:
            session.stop()
        except BaseException:
            with self._lock:
                self._failed_closes.add(key)
            raise
        else:
            with self._lock:
                self._closing.discard(key)
                self._failed_closes.discard(key)

    def stop(self, actor: VerifiedActor, ticket_id: str) -> dict[str, Any]:
        """Owner Stop, independent of whether the RDP grant still exists."""
        self._verify_owner(actor, ticket_id)
        key = (actor.owner_id, ticket_id)
        with self._lock:
            session = self._lookup(actor, ticket_id)
            if session is None:
                return {
                    "stopped": False,
                    "status": (
                        "stop_failed_locked" if key in self._failed_closes
                        else "stop_in_progress" if key in self._closing
                        else "no_live_session"
                    ),
                }
            del self._sessions[key]
            self._closing.add(key)
        self._stop_reserved(key, session)
        return {"stopped": True, "status": "stopped"}

    def stop_ticket(self, owner_id: str, ticket_id: str) -> None:
        """Internal SuperAgent ticket Stop with no bearer authority."""
        key = (owner_id, ticket_id)
        with self._lock:
            row = self._sessions.pop(key, None)
            if row is not None:
                self._closing.add(key)
        if row is not None:
            self._stop_reserved(key, row[1])

    def stop_all(self) -> None:
        """Attempt Stop on EVERY RDP channel even if one TLS close fails."""
        with self._lock:
            items = list(self._sessions.items())
            self._closing.update(key for key, _ in items)
            self._sessions.clear()
        first_failure: Exception | None = None
        for key, (_cookie, session) in items:
            try:
                self._stop_reserved(key, session)
            except Exception as exc:
                # Keep this key reserved until process teardown. Never
                # let one broken channel prevent stopping other desktops.
                if first_failure is None:
                    first_failure = exc
        if first_failure is not None:
            raise first_failure


__all__ = ["RDPLiveRegistry"]
