"""Bounded background ticket pool for a single elected SuperAgent leader.

This dispatcher runs independent tickets concurrently. It does not schedule
multiple LLM generations into a single local inference slot: the existing
inference scheduler and service-level LLM lock continue to enforce that.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class ParallelTicketPool:
    def __init__(self, max_workers: int = 4):
        if type(max_workers) is not int or not 1 <= max_workers <= 128:
            raise ValueError("Invalid node-local worker capacity")
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="leshy-superagent-job",
        )
        self._active: dict[Future[Any], str] = {}

    def __enter__(self) -> "ParallelTicketPool":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # The process lease must not be released until every in-flight worker
        # has finished: callers wrap this pool inside their lease lifetime.
        self._executor.shutdown(wait=True, cancel_futures=False)

    @property
    def active_ids(self) -> frozenset[str]:
        return frozenset(self._active.values())

    def reap(self) -> list[tuple[str, BaseException]]:
        """Collect completed futures, returning failures for diagnostic logs."""
        failures = []
        for future, ticket_id in list(self._active.items()):
            if not future.done():
                continue
            del self._active[future]
            try:
                future.result()
            except BaseException as exc:
                failures.append((ticket_id, exc))
        return failures

    def fill(self, next_due: Callable[[frozenset[str]], dict[str, Any] | None],
             execute: Callable[[dict[str, Any]], Any], *, stopped: Callable[[], bool],
             capacity: int | None = None) -> int:
        """Admit only available jobs; memory pressure never kills active work."""
        if capacity is not None and (type(capacity) is not int or capacity < 0):
            raise ValueError("Invalid worker admission capacity")
        allowed = self.max_workers if capacity is None else min(self.max_workers, capacity)
        assigned = 0
        while len(self._active) < allowed and not stopped():
            ticket = next_due(self.active_ids)
            if ticket is None:
                break
            ticket_id = str(ticket.get("id") or "")
            if not ticket_id or ticket_id in self.active_ids:
                raise ValueError("Очередь вернула пустой либо уже назначенный id")
            future = self._executor.submit(execute, ticket)
            self._active[future] = ticket_id
            assigned += 1
        return assigned
