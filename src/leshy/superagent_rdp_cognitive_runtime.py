"""Owner-verified SuperAgent ticket binding for read-only native RDP cognition.

Only a trusted, in-process SuperAgent caller can create this coordinator from
a live provider-authenticated actor and an already registered remote node.
No Web route, LLM-dispatched tool, permission grant, or RDP input is added.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .rdp_vision_bridge import RDPVisionPNG
from .rdp_local_vision import _validated_png

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
        # Exactly one bounded read-only LLM consumer per native stream.
        # The RDP frame/TLS callback never blocks on this model.
        self._cognitive_wake = threading.Event()
        self._cognitive_thread: threading.Thread | None = None
        self._cognitive_completed = 0
        self._cognitive_error_type = ""
        self._channel: PinnedRDPCredSSPChannel | None = None
        self._handshake_channel: PinnedRDPCredSSPChannel | None = None
        self._native_phase = "not_started"
        self._native_error_type = ""
        self._graphics_started = False
        self._latest_frame_update = 0
        self._last_proposal: RDPReadOnlyProposal | None = None
        # One real, decoded PNG only, never an HTTP input or a saved file.
        self._web_png: RDPVisionPNG | None = None
        self._web_png_at = 0.0
        self._last_web_update = 0

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
        # Runtime remote-admin kill switch is independent of the node grant.
        # Real SuperAgent instances always provide storage. Legacy isolated
        # service mocks may not have one and never expose a public endpoint.
        storage = getattr(self._service, "storage", None)
        if storage is not None:
            from .module_settings import module_enabled
            if not module_enabled(storage, "remote-admin"):
                raise PermissionError("Remote administration disabled")
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
            if self._native_phase in {"failed", "stream_finished", "stopped"}:
                raise PermissionError("Native RDP stream is terminal")
            if not self._inbox.offer(observation):
                raise PermissionError("RDP cognition is stopped")
            self._latest_frame_update = observation.update_count
            self._last_proposal = None
        self._cognitive_wake.set()
        return True

    def reason_once(self) -> RDPReadOnlyProposal | None:
        """Use the existing SuperAgent LLM; never execute model's proposal."""
        self.reauthorize()
        with self._lock:
            if self._native_phase in {"failed", "stream_finished", "stopped"}:
                return None
        result = self._inbox.reason_once(
            self._planner, owner_task=self._task,
            reauthorize=self.reauthorize,
        )
        self.reauthorize()
        with self._lock:
            if (self._cancel.is_set() or result is None
                    or self._native_phase in {"failed", "stream_finished", "stopped"}
                    or result.update_count != self._latest_frame_update):
                return None
            self._last_proposal = result
        return result

    def start_cognitive_worker(self) -> None:
        """Begin ONE supervised Planner LLM consumer; no remote actions."""
        self.reauthorize()
        with self._lock:
            if self._cancel.is_set() or self._cognitive_thread is not None:
                raise PermissionError("Native RDP cognitive worker unavailable")
            thread = threading.Thread(
                target=self._cognitive_loop,
                name="Leshy-RDP-cognition",
                daemon=True,
            )
            self._cognitive_thread = thread
        try:
            thread.start()
        except BaseException:
            self.stop()
            raise

    def _cognitive_loop(self) -> None:
        """Wait for real VLM observations; stale results never execute tools."""
        while not self._cancel.is_set():
            if not self._cognitive_wake.wait(0.5):
                # A graphics stream may complete naturally without Stop.
                # Do not leave a dormant daemon model thread per old session.
                with self._lock:
                    stream_finished = self._graphics_started and self._channel is None
                if stream_finished:
                    break
                continue
            self._cognitive_wake.clear()
            if self._cancel.is_set():
                break
            with self._lock:
                if self._native_phase in {"failed", "stream_finished", "stopped"}:
                    break
            try:
                proposal = self.reason_once()
                if proposal is not None:
                    with self._lock:
                        if (not self._cancel.is_set()
                                and self._native_phase not in {"failed", "stream_finished", "stopped"}):
                            self._cognitive_completed += 1
            except Exception as exc:
                with self._lock:
                    self._cognitive_error_type = type(exc).__name__[:80]
                # Fail closed on loss of owner rights or untrusted model
                # output. The Web view may not keep serving stale pixels.
                self.stop()
                break

    def last_proposal(self) -> RDPReadOnlyProposal | None:
        """Read-only review, revocable by Stop or a newer observation."""
        self.reauthorize()
        with self._lock:
            if (self._cancel.is_set() or self._channel is None
                    or self._native_phase in {"failed", "stream_finished", "stopped"}
                    or self._web_png is None
                    or time.monotonic() - self._web_png_at > 8.0):
                return None
            proposal = self._last_proposal
            if proposal is not None and proposal.update_count != self._last_web_update:
                return None
            return proposal

    def accept_native_png(self, frame: RDPVisionPNG) -> None:
        """Only called from real decoded native graphics; bounded memory.

        No owner DB check, model call or network I/O in the graphics callback.
        The native stream already rechecks owner permission immediately before
        forwarding this image. Web consumers recheck independently.
        """
        # Reject post-failure callbacks before expensive PNG validation.
        with self._lock:
            if (self._cancel.is_set() or self._channel is None
                    or self._native_phase in {"failed", "stream_finished", "stopped"}):
                raise PermissionError("Native RDP graphics stream is terminal")
        _validated_png(frame)
        with self._lock:
            if (self._cancel.is_set() or self._channel is None
                    or self._native_phase in {"failed", "stream_finished", "stopped"}):
                raise PermissionError("Native RDP graphics stream is terminal")
            if frame.update_count <= self._last_web_update:
                raise ValueError("Non-monotonic native RDP frame")
            self._last_web_update = frame.update_count
            self._web_png = frame
            self._web_png_at = time.monotonic()

    def current_native_png(self) -> RDPVisionPNG | None:
        """Return latest fresh native frame, never an inferred/synthetic image."""
        self.reauthorize()
        with self._lock:
            image = self._web_png
            if (self._cancel.is_set() or self._channel is None
                    or self._native_phase in {"failed", "stream_finished", "stopped"}
                    or image is None or time.monotonic() - self._web_png_at > 8.0):
                return None
        self.reauthorize()
        # Native failure can race the authorization recheck. Revalidate
        # under the session lock before returning a frame to the Web writer.
        with self._lock:
            if (self._cancel.is_set() or self._native_phase in {"failed", "stream_finished", "stopped"}
                    or self._channel is None or self._web_png is not image
                    or time.monotonic() - self._web_png_at > 8.0):
                return None
            return image

    def attach_native_handshake(self, channel: PinnedRDPCredSSPChannel) -> None:
        """Permit Stop to close a half-completed owner-approved handshake."""
        if not isinstance(channel, PinnedRDPCredSSPChannel):
            raise TypeError("Actual verified RDP socket required")
        self.reauthorize()
        with self._lock:
            if (self._cancel.is_set() or self._handshake_channel is not None
                    or self._channel is not None or self._graphics_started):
                raise PermissionError("Native RDP launch no longer allowed")
            self._handshake_channel = channel

    def clear_native_handshake(self, channel: PinnedRDPCredSSPChannel | None) -> None:
        with self._lock:
            if self._handshake_channel is channel:
                self._handshake_channel = None

    def set_native_phase(self, phase: str) -> None:
        """Protocol checkpoints, never claims proof of a usable desktop."""
        allowed = {
            "connecting", "tls_pinned", "credssp_bound", "credentials_sent",
            "mcs_settings", "mcs_joined", "client_info", "server_activated",
            "confirm_active", "finalized", "graphics", "stream_finished",
        }
        if phase not in allowed:
            raise ValueError("Unknown native RDP phase")
        self.reauthorize()
        with self._lock:
            if (self._cancel.is_set()
                    or self._native_phase in {"failed", "stream_finished", "stopped"}):
                raise PermissionError("Native RDP phase is terminal")
            self._native_phase = phase
            if phase == "stream_finished":
                self._web_png = None
                self._web_png_at = 0.0
                self._last_web_update = 0
                self._last_proposal = None
                self._cognitive_wake.set()

    def native_failure(self, exc_type: str) -> None:
        """Preserve first error class and immediately revoke cached pixels.

        No exception message, TLS payload, user credentials or old model
        proposal can escape into a post-failure Web status/image response.
        """
        with self._lock:
            if self._cancel.is_set():
                return
            if self._native_phase != "failed":
                self._native_phase = "failed"
                # Expose only a class-shaped identifier, never a message,
                # path, newline, credential, URL or arbitrary protocol text.
                safe = (
                    exc_type if type(exc_type) is str and
                    1 <= len(exc_type) <= 80 and exc_type.isascii() and
                    exc_type.isidentifier() else "Exception"
                )
                self._native_error_type = safe
            self._web_png = None
            self._web_png_at = 0.0
            self._last_web_update = 0
            self._last_proposal = None
        self._cognitive_wake.set()

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
            if self._handshake_channel is channel:
                self._handshake_channel = None
            self._channel = channel
        try:
            self.start_cognitive_worker()
            report = stream_native_rdp_with_local_vision(
                channel, reauthorize=self.reauthorize,
                on_observation=self.accept_observation,
                on_png_frame=self.accept_native_png,
                policy=policy, max_png_frames=max_png_frames,
                drain_timeout=drain_timeout,
            )
            self.reauthorize()
            return report
        finally:
            with self._lock:
                self._channel = None
                self._web_png = None
                self._web_png_at = 0.0
            self._cognitive_wake.set()
            channel.close()

    def stop(self) -> None:
        """Revoke future results immediately and request a bounded TLS Stop."""
        self._cancel.set()
        self._inbox.stop()
        wake = getattr(self, "_cognitive_wake", None)
        if wake is not None:
            wake.set()
        with self._lock:
            self._last_proposal = None
            self._web_png = None
            self._web_png_at = 0.0
            channel = self._channel
            pending = self._handshake_channel
            self._handshake_channel = None
            self._native_phase = "stopped"
        try:
            if channel is not None:
                try:
                    channel.stop_graphics_stream()
                finally:
                    # Requesting Stop is not proof the pinned TLS socket has
                    # closed. Close it before releasing the registry's ticket
                    # reservation, even when the receiver is between reads.
                    channel.close()
        finally:
            if pending is not None and pending is not channel:
                pending.close()

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
            "native_phase": self._native_phase,
            "native_error_type": self._native_error_type,
            "cognitive_running": bool(
                getattr(self, "_cognitive_thread", None)
                and self._cognitive_thread.is_alive()
            ),
            "cognitive_completed": getattr(self, "_cognitive_completed", 0),
            "cognitive_error_type": getattr(self, "_cognitive_error_type", ""),
            "graphics_active": (
                self._channel is not None and not self._cancel.is_set()
                and self._native_phase not in {"failed", "stream_finished", "stopped"}
            ),
            "frame_available": (
                self._channel is not None and not self._cancel.is_set()
                and self._native_phase not in {"failed", "stream_finished", "stopped"}
                and self._web_png is not None
                and time.monotonic() - self._web_png_at <= 8.0
            ),
            "frame_update": (
                self._last_web_update if self._web_png is not None
                and self._native_phase not in {"failed", "stream_finished", "stopped"}
                else 0
            ),
            "input_enabled": False,
            "windows_rdp_e2e_verified": False,
        }


__all__ = ["SuperAgentRDPCognitiveSession"]
