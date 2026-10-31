from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import itertools
import threading
import time
from typing import Iterator

from .diagnostics import event


PRIORITY_WEB = 100
PRIORITY_TELEGRAM = 70
PRIORITY_SYSTEM = 60
PRIORITY_RECOVERY = 30
PRIORITY_BACKGROUND = 10


@dataclass(slots=True)
class _Waiter:
    ticket: int
    priority: int
    source: str
    queued_at: float
    granted: bool = False


class InferenceScheduler:
    """Единый приоритетный диспетчер доступа к локальной LLM.

    Текущий локальный llama-server работает с одним inference-slot, поэтому
    scheduler сериализует запуск генераций и выбирает следующую задачу по
    приоритету. Уже выполняющийся пользовательский ответ не прерывается.

    Это намеренно отдельный архитектурный слой: Web/Telegram/recovery/фоновые
    задачи не должны самостоятельно проверять `_chat_lock`, делать sleep или
    соревноваться за backend. Позже реализацию можно заменить внешним broker
    (RabbitMQ/NATS/Redis Streams), сохранив этот контракт.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._sequence = itertools.count(1)
        self._waiters: list[_Waiter] = []
        self._active: _Waiter | None = None

    @staticmethod
    def priority_for(owner_id: str, *, background: bool = False, recovery: bool = False) -> int:
        owner = str(owner_id or "").lower()
        if recovery:
            return PRIORITY_RECOVERY
        if background:
            return PRIORITY_BACKGROUND
        if owner.startswith("web:"):
            return PRIORITY_WEB
        if owner.startswith("telegram:"):
            return PRIORITY_TELEGRAM
        return PRIORITY_SYSTEM

    def _next_waiter(self) -> _Waiter | None:
        if not self._waiters:
            return None
        # Больше priority = важнее; при равенстве работает FIFO.
        return min(self._waiters, key=lambda item: (-item.priority, item.ticket))

    def acquire(
        self,
        *,
        source: str,
        priority: int,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> _Waiter:
        waiter = _Waiter(
            ticket=next(self._sequence),
            priority=int(priority),
            source=str(source or "unknown")[:160],
            queued_at=time.monotonic(),
        )
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            self._waiters.append(waiter)
            event(
                "inference_queue_entered",
                source=waiter.source,
                priority=waiter.priority,
                queue_depth=len(self._waiters),
                active_source=self._active.source if self._active else "",
            )
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._waiters = [item for item in self._waiters if item is not waiter]
                    self._condition.notify_all()
                    raise InterruptedError("LLM-запрос отменён до запуска")
                if deadline is not None and time.monotonic() >= deadline:
                    self._waiters = [item for item in self._waiters if item is not waiter]
                    self._condition.notify_all()
                    raise TimeoutError("Ожидание локальной LLM превысило допустимое время")
                if self._active is None and self._next_waiter() is waiter:
                    self._waiters.remove(waiter)
                    waiter.granted = True
                    self._active = waiter
                    event(
                        "inference_queue_granted",
                        source=waiter.source,
                        priority=waiter.priority,
                        wait_ms=int((time.monotonic() - waiter.queued_at) * 1000),
                        queue_depth=len(self._waiters),
                    )
                    return waiter
                wait_for = 0.1
                if deadline is not None:
                    wait_for = max(0.01, min(wait_for, deadline - time.monotonic()))
                self._condition.wait(wait_for)

    def release(self, waiter: _Waiter) -> None:
        with self._condition:
            if self._active is waiter:
                held_ms = int((time.monotonic() - waiter.queued_at) * 1000)
                self._active = None
                event(
                    "inference_queue_released",
                    source=waiter.source,
                    priority=waiter.priority,
                    total_ms=held_ms,
                    queue_depth=len(self._waiters),
                )
            self._condition.notify_all()

    @contextmanager
    def slot(
        self,
        *,
        source: str,
        priority: int,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> Iterator[None]:
        waiter = self.acquire(
            source=source,
            priority=priority,
            cancel_event=cancel_event,
            timeout=timeout,
        )
        try:
            yield
        finally:
            self.release(waiter)

    def status(self) -> dict:
        with self._condition:
            ordered = sorted(self._waiters, key=lambda item: (-item.priority, item.ticket))
            return {
                "active": None if self._active is None else {
                    "source": self._active.source,
                    "priority": self._active.priority,
                },
                "waiting": [
                    {
                        "source": item.source,
                        "priority": item.priority,
                        "wait_ms": int((time.monotonic() - item.queued_at) * 1000),
                    }
                    for item in ordered
                ],
                "queue_depth": len(ordered),
            }


def get_inference_scheduler(app) -> InferenceScheduler:
    current = getattr(app, "inference_scheduler", None)
    if isinstance(current, InferenceScheduler):
        return current
    lock = getattr(app, "_inference_scheduler_init_lock", None)
    if lock is None:
        lock = threading.RLock()
        setattr(app, "_inference_scheduler_init_lock", lock)
    with lock:
        current = getattr(app, "inference_scheduler", None)
        if isinstance(current, InferenceScheduler):
            return current
        current = InferenceScheduler()
        setattr(app, "inference_scheduler", current)
        event("inference_scheduler_ready", mode="priority-single-slot")
        return current


__all__ = [
    "InferenceScheduler",
    "get_inference_scheduler",
    "PRIORITY_WEB",
    "PRIORITY_TELEGRAM",
    "PRIORITY_SYSTEM",
    "PRIORITY_RECOVERY",
    "PRIORITY_BACKGROUND",
]
