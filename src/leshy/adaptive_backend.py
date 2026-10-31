from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any

from .diagnostics import event, exception
from .llm import ChatMessage, LLMUnavailable


@dataclass
class _BackendState:
    name: str
    provider: Any
    failed_until: float = 0.0
    failures: int = 0
    last_latency_ms: int | None = None


def _hardware_rank(name: str) -> int:
    lowered = name.lower()
    if "gpu" in lowered or "vulkan" in lowered or "cuda" in lowered or "rocm" in lowered:
        return 0
    if "npu" in lowered or "openvino" in lowered:
        return 1
    if "cpu" in lowered or "native" in lowered:
        return 2
    return 3


class AdaptiveLLM:
    """GPU-first failover для интерактивного LLM.

    На каждом новом пользовательском запросе первым выбирается доступный GPU.
    Если GPU находится в cooldown после реальной ошибки, используется NPU, затем
    CPU. После окончания cooldown GPU снова автоматически получает приоритет.
    """

    def __init__(self, candidates: list[tuple[str, Any]], *, cooldown_seconds: float = 180.0):
        if not candidates:
            raise ValueError("Нужен хотя бы один LLM backend")
        self._states = [_BackendState(name, provider) for name, provider in candidates]
        self.cooldown_seconds = max(5.0, float(cooldown_seconds))
        self._preferred = self._states[0].name
        self._lock = threading.RLock()
        self.model = "adaptive:" + ",".join(state.name for state in self._states)

    def _ordered(self) -> list[_BackendState]:
        now = time.monotonic()
        with self._lock:
            healthy = [state for state in self._states if state.failed_until <= now]
            cooling = [state for state in self._states if state.failed_until > now]
            healthy.sort(key=lambda state: (_hardware_rank(state.name), state.failures, state.name))
            if healthy:
                return healthy
            return sorted(cooling, key=lambda state: state.failed_until)[:1]

    def chat(self, messages: list[ChatMessage]) -> str:
        errors: list[str] = []
        for state in self._ordered():
            started = time.monotonic()
            try:
                text = state.provider.chat(messages)
            except Exception as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                with self._lock:
                    state.failures += 1
                    state.last_latency_ms = latency_ms
                    state.failed_until = time.monotonic() + self.cooldown_seconds
                exception(
                    "adaptive_llm_backend_failed",
                    exc,
                    backend=state.name,
                    latency_ms=latency_ms,
                    cooldown_seconds=self.cooldown_seconds,
                )
                errors.append(f"{state.name}: {exc}")
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            with self._lock:
                switched = self._preferred != state.name
                self._preferred = state.name
                state.failures = 0
                state.failed_until = 0.0
                state.last_latency_ms = latency_ms
            event("adaptive_llm_backend_ok", backend=state.name, latency_ms=latency_ms, switched=switched)
            return text

        raise LLMUnavailable("Все локальные LLM backend недоступны: " + "; ".join(errors[-3:]))

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            return {
                "preferred": self._preferred,
                "backends": [
                    {
                        "name": state.name,
                        "cooldown_remaining_seconds": max(0.0, round(state.failed_until - now, 1)),
                        "failures": state.failures,
                        "last_latency_ms": state.last_latency_ms,
                    }
                    for state in self._states
                ],
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
    """GPU-first failover embeddings: GPU → NPU → CPU."""

    def __init__(self, candidates: list[tuple[str, Any]], *, cooldown_seconds: float = 180.0):
        if not candidates:
            raise ValueError("Нужен хотя бы один embedding backend")
        self._states = [_BackendState(name, provider) for name, provider in candidates]
        self.cooldown_seconds = max(5.0, float(cooldown_seconds))
        self._preferred = self._states[0].name
        self._lock = threading.RLock()

        provider_models = [str(getattr(state.provider, "model", state.name)) for state in self._states]
        basenames = [value.split(":", 1)[-1] for value in provider_models]
        canonical = basenames[0] if basenames and all(name == basenames[0] for name in basenames) else "mixed"
        self.model = f"adaptive:{canonical}"
        self.compatible_models = tuple(dict.fromkeys([self.model, *provider_models]))

    def available(self) -> bool:
        for state in self._states:
            available = getattr(state.provider, "available", None)
            try:
                if available is None or bool(available()):
                    return True
            except Exception:
                continue
        return False

    def _ordered(self) -> list[_BackendState]:
        now = time.monotonic()
        with self._lock:
            healthy = [state for state in self._states if state.failed_until <= now]
            cooling = [state for state in self._states if state.failed_until > now]
            healthy.sort(key=lambda state: (_hardware_rank(state.name), state.failures, state.name))
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
                with self._lock:
                    state.failures += 1
                    state.last_latency_ms = latency_ms
                    state.failed_until = time.monotonic() + self.cooldown_seconds
                exception(
                    "adaptive_embedding_backend_failed",
                    exc,
                    backend=state.name,
                    latency_ms=latency_ms,
                    cooldown_seconds=self.cooldown_seconds,
                )
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
            return {
                "preferred": self._preferred,
                "model": self.model,
                "compatible_models": list(self.compatible_models),
                "backends": [
                    {
                        "name": state.name,
                        "cooldown_remaining_seconds": max(0.0, round(state.failed_until - now, 1)),
                        "failures": state.failures,
                        "last_latency_ms": state.last_latency_ms,
                    }
                    for state in self._states
                ],
            }

    def close(self) -> None:
        for state in self._states:
            closer = getattr(state.provider, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
