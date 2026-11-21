"""Bounded, owner-scoped RDP vision worker; no remote input or public API.

A native RDP TLS graphics callback must never block on a local vision model.
Submit only in-process PNGs from the owner-authorized read-only RDP bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from typing import Callable

from .rdp_graphics_stream import GraphicsStreamPolicy
from .rdp_credssp_channel import PinnedRDPCredSSPChannel
from .rdp_local_vision import (
    RDPVisualObservation, describe_native_rdp_png, _validated_png,
)
from .rdp_vision_bridge import RDPVisionPNG, RDPVisionResult, stream_native_rdp_png


@dataclass(frozen=True)
class RDPVisionWorkerStatus:
    started: bool
    stopped: bool
    busy: bool
    pending: bool
    submitted: int
    dropped: int
    delivered: int
    failure_type: str
    input_enabled: bool = False


@dataclass(frozen=True)
class RDPVisionStreamReport:
    graphics: RDPVisionResult
    worker: RDPVisionWorkerStatus
    drained: bool
    input_enabled: bool = False
    desktop_e2e_verified: bool = False


class RDPVisionWorker:
    """One inference thread, at most one pending + one in-flight PNG.

    Newer frames supersede queued frames; inference already in flight cannot
    be forcibly interrupted, but its stale result is never delivered.
    The output callback must be trusted, quick and nonblocking. No arbitrary
    callback supplied by a model may be used as authorization.
    """

    def __init__(
        self, *,
        reauthorize: Callable[[], None],
        on_observation: Callable[[RDPVisualObservation], None],
        infer: Callable[..., RDPVisualObservation] = describe_native_rdp_png,
    ) -> None:
        if not all(callable(x) for x in (reauthorize, on_observation, infer)):
            raise TypeError("Trusted authorization, inference and sink are required")
        self._authorize = reauthorize
        self._sink = on_observation
        self._infer = infer
        self._condition = threading.Condition(threading.RLock())
        self._worker: threading.Thread | None = None
        self._started = False
        self._stopped = False
        self._busy = False
        self._pending: tuple[int, RDPVisionPNG] | None = None
        self._latest = 0
        self._last_update = 0
        self._submitted = 0
        self._dropped = 0
        self._delivered = 0
        self._failure_type = ""

    def start(self) -> None:
        with self._condition:
            if self._started or self._stopped:
                raise RuntimeError("RDP vision worker cannot be restarted")
            # Do not execute a potentially slow owner check while holding
            # a mutex needed by the network frame producer.
        self._authorize()
        with self._condition:
            if self._started or self._stopped:
                raise RuntimeError("RDP vision worker state changed")
            self._worker = threading.Thread(
                target=self._run, name="Leshy-RDP-Vision", daemon=True,
            )
            self._started = True
            self._worker.start()

    def submit(self, frame: RDPVisionPNG) -> bool:
        """Nonblocking upload from a trusted graphics callback.

        This performs only bounded metadata validation, no network, disk,
        authorization query, PNG decoding or model inference.
        """
        _validated_png(frame)
        with self._condition:
            if not self._started or self._stopped or self._failure_type:
                return False
            if frame.update_count <= self._last_update:
                raise ValueError("RDP PNG frames must have increasing update count")
            self._last_update = frame.update_count
            self._latest += 1
            self._submitted += 1
            if self._pending is not None:
                self._dropped += 1
            self._pending = (self._latest, frame)
            self._condition.notify()
            return True

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                seq, frame = self._pending
                self._pending = None
                self._busy = True
            try:
                self._authorize()
                observation = self._infer(frame, reauthorize=self._authorize)
                # Recheck immediately before handing any result to the
                # trusted consumer. Local model text is always untrusted.
                self._authorize()
                with self._condition:
                    if self._stopped or seq != self._latest:
                        self._dropped += 1
                    else:
                        if (type(observation) is not RDPVisualObservation
                                or observation.input_enabled is not False
                                or observation.model_output_untrusted is not True
                                or observation.e2e_verified is not False
                                or observation.update_count != frame.update_count
                                or observation.frame_sha256 != frame.content_sha256
                                or observation.width != frame.width
                                or observation.height != frame.height):
                            raise ValueError("Untrusted or mismatched RDP model result")
                        # An explicitly trusted nonblocking sink is called
                        # under the small cancellation boundary. Stop cannot
                        # return before a started delivery has completed.
                        self._sink(observation)
                        self._delivered += 1
            except Exception as exc:
                # Never retry failed inference, authorization or sink effects.
                # Error names are safe to expose; never return exception text.
                with self._condition:
                    self._failure_type = type(exc).__name__
                    self._stopped = True
                    if self._pending is not None:
                        self._dropped += 1
                        self._pending = None
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

    def status(self) -> RDPVisionWorkerStatus:
        with self._condition:
            return RDPVisionWorkerStatus(
                self._started, self._stopped, self._busy,
                self._pending is not None, self._submitted,
                self._dropped, self._delivered, self._failure_type,
            )

    def drain(self, *, timeout: float = 45.0) -> bool:
        """Wait outside the TLS receive loop; never wait without a bound."""
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 <= timeout <= 60:
            raise ValueError("Invalid bounded RDP vision drain timeout")
        with self._condition:
            if not self._started:
                raise RuntimeError("RDP vision worker not started")
            ok = self._condition.wait_for(
                lambda: (not self._busy and self._pending is None)
                or self._stopped, timeout=float(timeout),
            )
            return bool(ok and not self._busy and self._pending is None
                        and not self._failure_type)

    def stop(self) -> None:
        """Idempotent, nonblocking cancellation; no later sink delivery."""
        with self._condition:
            self._stopped = True
            if self._pending is not None:
                self._dropped += 1
                self._pending = None
            self._condition.notify_all()


def stream_native_rdp_with_local_vision(
    channel: PinnedRDPCredSSPChannel,
    *, reauthorize: Callable[[], None],
    on_observation: Callable[[RDPVisualObservation], None],
    policy: GraphicsStreamPolicy | None = None,
    max_png_frames: int = 32,
    drain_timeout: float = 45.0,
) -> RDPVisionStreamReport:
    """Connect existing RDP graphics to its local VLM using a coalescing worker.

    The caller is a trusted synchronous owner-scoped coordinator; it must not
    run this on a Web request thread. The underlying RDP stream itself is not
    blocked by the model. On any error the pending inference is cancelled.
    """
    worker = RDPVisionWorker(
        reauthorize=reauthorize, on_observation=on_observation,
    )
    try:
        worker.start()
        def submit_or_fail(frame: RDPVisionPNG) -> None:
            if not worker.submit(frame):
                raise RuntimeError("RDP vision worker stopped accepting frames")

        graphics = stream_native_rdp_png(
            channel, submit_or_fail, reauthorize=reauthorize,
            policy=policy, max_png_frames=max_png_frames,
        )
        drained = worker.drain(timeout=drain_timeout)
        worker.stop()
        state = worker.status()
        if state.failure_type:
            raise RuntimeError("RDP vision inference failed; no automatic retry")
        return RDPVisionStreamReport(graphics, state, drained)
    finally:
        worker.stop()
        # If starting the worker fails, the graphics bridge never ran and
        # has not yet closed this sensitive already authenticated channel.
        channel.close()


__all__ = [
    "RDPVisionWorker", "RDPVisionWorkerStatus", "RDPVisionStreamReport",
    "stream_native_rdp_with_local_vision",
]
