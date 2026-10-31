from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any

from .diagnostics import event, exception
from .hardware import HardwareConfigStore
from .llm import ChatMessage, LLMUnavailable
from .persona import with_leshy_identity


@dataclass
class _BackendState:
    name: str
    provider: Any
    failed_until: float = 0.0
    failures: int = 0
    last_latency_ms: int | None = None
    quarantined: bool = False
    quarantine_reason: str = ""


def _hardware_kind(name: str) -> str:
    lowered = name.lower()
    if "gpu" in lowered or "vulkan" in lowered or "cuda" in lowered or "rocm" in lowered:
        return "gpu"
    if "npu" in lowered or "openvino" in lowered:
        return "npu"
    if "cpu" in lowered or "native" in lowered:
        return "cpu"
    return "other"


def _fatal_npu_failure(name: str, exc: BaseException) -> bool:
    """Return True for NPU/OpenVINO compute failures that must not stall chat again."""
    if _hardware_kind(name) != "npu":
        return False
    text = str(exc).upper().replace("-", "_").replace(" ", "_")
    markers = (
        "OUT_OF_HOST_MEMORY",
        "OUT_OF_DEVICE_MEMORY",
        "DEVICE_LOST",
        "DEVICE_NOT_AVAILABLE",
        "INFERENCE_ENGINE_ERROR",
        "OPENVINO_ERROR",
    )
    return any(marker in text for marker in markers)


class AdaptiveLLM:
    """Failover between resident local LLM backends with hardware-mode routing.

    The mode selected in Web UI is stored in LeshyBrain/settings/hardware.json.
    Routing is re-read for every request, so changing the mode affects the next
    answer without rebuilding the application. CPU remains a safety fallback for
    accelerator modes so a broken driver does not leave the user without chat.
    """

    def __init__(self, candidates: list[tuple[str, Any]], *, cooldown_seconds: float = 180.0,
                 priority: tuple[str, ...] = ("gpu", "npu", "cpu", "other")):
        if not candidates:
            raise ValueError("Нужен хотя бы один LLM backend")
        self._states = [_BackendState(name, provider) for name, provider in candidates]
        self.cooldown_seconds = max(5.0, float(cooldown_seconds))
        self.priority = tuple(priority)
        self._priority_rank = {kind: index for index, kind in enumerate(self.priority)}
        self._preferred = self._states[0].name
        self._lock = threading.RLock()
        self.model = "adaptive:" + ",".join(state.name for state in self._states)
        self._hardware_store = self._discover_hardware_store()

    def _discover_hardware_store(self):
        for state in self._states:
            manager = getattr(state.provider, "manager", None)
            brain_dir = getattr(manager, "brain_dir", None)
            if brain_dir is not None:
                try:
                    return HardwareConfigStore(brain_dir)
                except Exception:
                    return None
        return None

    def _hardware_mode(self) -> str:
        if self._hardware_store is None:
            return "auto"
        try:
            return str(self._hardware_store.load().mode or "auto").lower()
        except Exception:
            return "auto"

    def _routing_priority(self) -> tuple[str, ...]:
        mode = self._hardware_mode()
        routes = {
            "cpu": ("cpu",),
            "gpu": ("gpu", "cpu"),
            "npu": ("npu", "cpu"),
            "cpu_gpu": ("gpu", "cpu"),
            "cpu_npu": ("npu", "cpu"),
            "gpu_npu": ("gpu", "npu", "cpu"),
            "cpu_gpu_npu": ("gpu", "npu", "cpu"),
        }
        return routes.get(mode, self.priority)

    @staticmethod
    def _rank_for(state: _BackendState, priority: tuple[str, ...]) -> tuple[int, int, str]:
        rank = {kind: index for index, kind in enumerate(priority)}
        return (rank.get(_hardware_kind(state.name), len(rank)), state.failures, state.name)

    def _rank(self, state: _BackendState) -> tuple[int, int, str]:
        return self._rank_for(state, self._routing_priority())

    def _ordered(self) -> list[_BackendState]:
        now = time.monotonic()
        routing = self._routing_priority()
        allowed = set(routing)
        with self._lock:
            usable = [
                state for state in self._states
                if not state.quarantined and _hardware_kind(state.name) in allowed
            ]
            # If a requested accelerator is not present at all, retain a CPU
            # safety path rather than failing every chat request immediately.
            if not usable:
                usable = [state for state in self._states if not state.quarantined and _hardware_kind(state.name) == "cpu"]
            healthy = [state for state in usable if state.failed_until <= now]
            cooling = [state for state in usable if state.failed_until > now]
            healthy.sort(key=lambda state: self._rank_for(state, routing))
            if healthy:
                return healthy
            return sorted(cooling, key=lambda state: state.failed_until)[:1]

    def _mark_failure(self, state: _BackendState, exc: BaseException, latency_ms: int) -> bool:
        fatal = _fatal_npu_failure(state.name, exc)
        with self._lock:
            state.failures += 1
            state.last_latency_ms = latency_ms
            if fatal:
                state.quarantined = True
                state.quarantine_reason = str(exc)
                state.failed_until = float("inf")
            else:
                state.failed_until = time.monotonic() + self.cooldown_seconds
        return fatal

    def _mark_success(self, state: _BackendState, latency_ms: int) -> bool:
        with self._lock:
            switched = self._preferred != state.name
            self._preferred = state.name
            state.failures = 0
            state.failed_until = 0.0
            state.last_latency_ms = latency_ms
        return switched

    def warmup(self) -> str | None:
        """Preload the highest-priority usable backend and keep it resident."""
        for state in self._ordered():
            warmer = getattr(state.provider, "warmup", None)
            if not callable(warmer):
                continue
            started = time.monotonic()
            try:
                warmer()
            except Exception as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                fatal = self._mark_failure(state, exc, latency_ms)
                exception(
                    "adaptive_llm_warmup_failed",
                    exc,
                    backend=state.name,
                    latency_ms=latency_ms,
                    cooldown_seconds=self.cooldown_seconds,
                    quarantined=fatal,
                )
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            switched = self._mark_success(state, latency_ms)
            event("adaptive_llm_warmup_ok", backend=state.name, latency_ms=latency_ms, switched=switched)
            return state.name
        return None

    def chat(self, messages: list[ChatMessage]) -> str:
        prepared_messages = with_leshy_identity(messages)
        errors: list[str] = []
        routing = self._routing_priority()
        for state in self._ordered():
            started = time.monotonic()
            try:
                text = state.provider.chat(prepared_messages)
            except Exception as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                fatal = self._mark_failure(state, exc, latency_ms)
                exception("adaptive_llm_backend_failed", exc, backend=state.name, latency_ms=latency_ms,
                          cooldown_seconds=self.cooldown_seconds, quarantined=fatal,
                          hardware_mode=self._hardware_mode(), routing=list(routing))
                errors.append(f"{state.name}: {exc}")
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            switched = self._mark_success(state, latency_ms)
            event("adaptive_llm_backend_ok", backend=state.name, latency_ms=latency_ms, switched=switched,
                  hardware_mode=self._hardware_mode(), routing=list(routing))
            return text
        raise LLMUnavailable("Все выбранные локальные LLM backend недоступны: " + "; ".join(errors[-3:]))

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        mode = self._hardware_mode()
        routing = self._routing_priority()
        with self._lock:
            rows = []
            for state in self._states:
                resident = None
                resident_status = getattr(state.provider, "resident_status", None)
                if callable(resident_status):
                    try:
                        resident = resident_status()
                    except Exception:
                        resident = None
                rows.append({
                    "name": state.name,
                    "kind": _hardware_kind(state.name),
                    "selected": _hardware_kind(state.name) in set(routing),
                    "cooldown_remaining_seconds": None if state.quarantined else max(0.0, round(state.failed_until - now, 1)),
                    "failures": state.failures,
                    "last_latency_ms": state.last_latency_ms,
                    "quarantined": state.quarantined,
                    "quarantine_reason": state.quarantine_reason,
                    "resident": resident,
                })
            return {
                "preferred": self._preferred,
                "priority": list(self.priority),
                "hardware_mode": mode,
                "effective_routing": list(routing),
                "backends": rows,
            }

    def close(self) -> None:
        for state in self._states:
            closer = getattr(state.provider, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass


class AdaptiveEmbeddings:
    """GPU-first failover embeddings: GPU -> NPU -> CPU."""

    def __init__(self, candidates: list[tuple[str, Any]], *, cooldown_seconds: float = 180.0,
                 priority: tuple[str, ...] = ("gpu", "npu", "cpu", "other")):
        if not candidates:
            raise ValueError("Нужен хотя бы один embedding backend")
        self._states = [_BackendState(name, provider) for name, provider in candidates]
        self.cooldown_seconds = max(5.0, float(cooldown_seconds))
        self.priority = tuple(priority)
        self._priority_rank = {kind: index for index, kind in enumerate(self.priority)}
        self._preferred = self._states[0].name
        self._lock = threading.RLock()
        provider_models = [str(getattr(state.provider, "model", state.name)) for state in self._states]
        basenames = [value.split(":", 1)[-1] for value in provider_models]
        canonical = basenames[0] if basenames and all(name == basenames[0] for name in basenames) else "mixed"
        self.model = f"adaptive:{canonical}"
        self.compatible_models = tuple(dict.fromkeys([self.model, *provider_models]))

    def available(self) -> bool:
        for state in self._states:
            if state.quarantined:
                continue
            available = getattr(state.provider, "available", None)
            try:
                if available is None or bool(available()):
                    return True
            except Exception:
                continue
        return False

    def _rank(self, state: _BackendState) -> tuple[int, int, str]:
        return (self._priority_rank.get(_hardware_kind(state.name), len(self._priority_rank)), state.failures, state.name)

    def _ordered(self) -> list[_BackendState]:
        now = time.monotonic()
        with self._lock:
            usable = [state for state in self._states if not state.quarantined]
            healthy = [state for state in usable if state.failed_until <= now]
            cooling = [state for state in usable if state.failed_until > now]
            healthy.sort(key=self._rank)
            if healthy:
                return healthy
            return sorted(cooling, key=lambda state: state.failed_until)[:1]

    def embed(self, text: str) -> list[float]:
        errors: list[str] = []
        for state in self._ordered():
            started = time.monotonic()
            try:
                vector = state.provider.embed(text)
            except Exception as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                fatal = _fatal_npu_failure(state.name, exc)
                with self._lock:
                    state.failures += 1
                    state.last_latency_ms = latency_ms
                    if fatal:
                        state.quarantined = True
                        state.quarantine_reason = str(exc)
                        state.failed_until = float("inf")
                    else:
                        state.failed_until = time.monotonic() + self.cooldown_seconds
                exception("adaptive_embedding_backend_failed", exc, backend=state.name, latency_ms=latency_ms,
                          cooldown_seconds=self.cooldown_seconds, quarantined=fatal)
                errors.append(f"{state.name}: {exc}")
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            with self._lock:
                switched = self._preferred != state.name
                self._preferred = state.name
                state.failures = 0
                state.failed_until = 0.0
                state.last_latency_ms = latency_ms
            event("adaptive_embedding_backend_ok", backend=state.name, latency_ms=latency_ms, switched=switched)
            return vector
        raise RuntimeError("Все embedding backend недоступны: " + "; ".join(errors[-3:]))

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            return {"preferred": self._preferred, "priority": list(self.priority), "model": self.model,
                    "compatible_models": list(self.compatible_models), "backends": [
                {"name": state.name, "kind": _hardware_kind(state.name),
                 "cooldown_remaining_seconds": None if state.quarantined else max(0.0, round(state.failed_until - now, 1)),
                 "failures": state.failures, "last_latency_ms": state.last_latency_ms,
                 "quarantined": state.quarantined, "quarantine_reason": state.quarantine_reason}
                for state in self._states]}

    def close(self) -> None:
        for state in self._states:
            closer = getattr(state.provider, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass