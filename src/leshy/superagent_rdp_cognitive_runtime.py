"""Owner-verified SuperAgent ticket binding for read-only native RDP cognition.

Only a trusted, in-process SuperAgent caller can create this coordinator from
a live provider-authenticated actor and an already registered remote node.
No Web route, LLM-dispatched tool, permission grant, or RDP input is added.
"""
from __future__ import annotations

import threading
from typing import Any

from .planner import Planner
from .rdp_cognitive_bridge import RDPObservationInbox, RDPReadOnlyProposal
from .rdp_local_vision import RDPVisualObservation
from .rdp_vision_worker import (
    RDPVisionStreamReport, stream_native_rdp_with_local_vision,
)
from .rdp_credssp_channel import PinnedRDPCredSSPChannel
from .rdp_graphics_stream import GraphicsStreamPolicy
from .superagent_remote_permissions import (
    RemotePermissionPolicy, VerifiedActor,
)


class _SuperAgentTicketLLM:
    """Serialize through the existing SuperAgent's real model lock."""

    def __init__(self, service: Any, ticket_id: str, authorize) -> None:
        self._service = service
        self._ticket_id = ticket_id
        self._authorize = authorize

    def chat(self, messages) -> str:
        self._authorize()
        # SuperAgentService._chat_inference holds its established LLM lock
        # while calling the real service.app.llm; no alternate model selected.
        value = self._service._chat_inference(self._ticket_id, messages)
        self._authorize()
        return value


class SuperAgentRDPCognitiveSession:
    """Ticket-bound, cancellable read-only coordination on a registered node.

    Requires a provider-verified Web actor, registered node and live RDP grant
    at construction and at every result/stream boundary. A ticket's original
    owner request is immutable input. The LLM cannot select owner, node,
    credentials, approval scopes or the authorized task.
    """

    def __init__(
        self,
        service: Any,
        *,
        ticket_id: str,
        actor: VerifiedActor,
        node_id: str,
        observed_fingerprint: str,
    ) -> None:
        if not isinstance(actor, VerifiedActor):
            raise PermissionError("Verified provider actor required")
        if type(ticket_id) is not str or not ticket_id.startswith("SA-") or len(ticket_id) > 100:
            raise ValueError("Valid SuperAgent ticket required")
        if not callable(getattr(service, "_chat_inference", None)):
            raise TypeError("Existing SuperAgent model inference required")
        identity = getattr(getattr(service, "app", None), "owner_identity", None)
        if identity is None:
            raise PermissionError("Verified owner identity provider unavailable")
        self._service = service
        self._actor = actor
        self._ticket_id = ticket_id
        self._node_id = node_id
        self._observed_fingerprint = observed_fingerprint
        self._policy = RemotePermissionPolicy(identity)
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._inbox = RDPObservationInbox()
        self._channel: PinnedRDPCredSSPChannel | None = None
        self._graphics_started = False
        self._latest_frame_update = 0
        self._last_proposal: RDPReadOnlyProposal | None = None

        # A currently working, owner-scoped SuperAgent task is mandatory.
        self._policy._check_actor(actor)
        self._policy.require(
            actor, node_id, "rdp", observed_fingerprint=observed_fingerprint
        )
        ticket = service.get(ticket_id, owner_id=actor.owner_id)
        original = str(ticket.get("request_text") or "").strip()
        if (ticket.get("state") != "working"
                or not 1 <= len(original) <= 1200):
            raise PermissionError("Only an active, bounded owner task may observe RDP")
        self._task = original
        self.reauthorize()

        # This planner owns no tools. Its LLM is the SAME SuperAgent model.
        self._planner = Planner(
            _SuperAgentTicketLLM(service, ticket_id, self.reauthorize), None
        )

    def reauthorize(self) -> None:
        """Live authenticated session + node grant + ticket Stop at each call."""
        if self._cancel.is_set():
            raise PermissionError("RDP cognition stopped")
        stop = getattr(self._service, "_stop", None)
        if isinstance(stop, threading.Event) and stop.is_set():
            raise PermissionError("SuperAgent worker stopped")
        self._policy.require(
            self._actor, self._node_id, "rdp",
            observed_fingerprint=self._observed_fingerprint,
        )
        ticket = self._service.get(self._ticket_id, owner_id=self._actor.owner_id)
        if (ticket.get("state") != "working"
                or str(ticket.get("request_text") or "").strip() != self._task):
            raise PermissionError("SuperAgent task stopped or changed")

    def accept_observation(self, observation: RDPVisualObservation) -> bool:
        """In-process producer method; no LLM inference in the callback."""
        self.reauthorize()
        with self._lock:
            if not self._inbox.offer(observation):
                raise PermissionError("RDP cognition is stopped")
            self._latest_frame_update = observation.update_count
            self._last_proposal = None
        return True

    def reason_once(self) -> RDPReadOnlyProposal | None:
        """Use the existing SuperAgent LLM; never execute model's proposal."""
        self.reauthorize()
        result = self._inbox.reason_once(
            self._planner, owner_task=self._task,
            reauthorize=self.reauthorize,
        )
        self.reauthorize()
        with self._lock:
            if (self._cancel.is_set() or result is None
                    or result.update_count != self._latest_frame_update):
                return None
            self._last_proposal = result
        return result

    def last_proposal(self) -> RDPReadOnlyProposal | None:
        """Read-only review, revocable by Stop or a newer observation."""
        self.reauthorize()
        with self._lock:
            return self._last_proposal

    def receive_graphics(
        self,
        channel: PinnedRDPCredSSPChannel,
        *,
        policy: GraphicsStreamPolicy | None = None,
        max_png_frames: int = 32,
        drain_timeout: float = 45.0,
    ) -> RDPVisionStreamReport:
        """Blocking trusted coordinator; run outside HTTP/TLS/model callbacks.

        Requires an already authenticated/pinned/finalized native RDP channel.
        This does not open a socket or infer connection security from the SSH
        enrollment fingerprint; the RDP transport enforces its own TLS pin.
        """
        if not isinstance(channel, PinnedRDPCredSSPChannel):
            raise TypeError("Verified native RDP transport required")
        self.reauthorize()
        with self._lock:
            if self._cancel.is_set() or self._graphics_started:
                raise PermissionError("Native RDP graphics session unavailable")
            self._graphics_started = True
            self._channel = channel
        try:
            report = stream_native_rdp_with_local_vision(
                channel, reauthorize=self.reauthorize,
                on_observation=self.accept_observation,
                policy=policy, max_png_frames=max_png_frames,
                drain_timeout=drain_timeout,
            )
            self.reauthorize()
            return report
        finally:
            with self._lock:
                self._channel = None
            channel.close()

    def stop(self) -> None:
        """Revoke future results immediately and request a bounded TLS Stop."""
        self._cancel.set()
        self._inbox.stop()
        with self._lock:
            self._last_proposal = None
            channel = self._channel
        if channel is not None:
            channel.stop_graphics_stream()

    def snapshot(self) -> dict[str, Any]:
        """No screenshots, secrets or model replies in control-plane status."""
        state = self._inbox.snapshot()
        return {
            "ticket_id": self._ticket_id,
            "node_id": self._node_id,
            "stopped": self._cancel.is_set() or bool(state["stopped"]),
            "busy": state["busy"],
            "pending": state["pending"],
            "dropped": state["dropped"],
            "graphics_started": self._graphics_started,
            "graphics_active": self._channel is not None,
            "input_enabled": False,
            "windows_rdp_e2e_verified": False,
        }


__all__ = ["SuperAgentRDPCognitiveSession"]
