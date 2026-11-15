"""Safe parallel read-only batches inside the persisted SuperAgent workflow."""
from __future__ import annotations

import json
from typing import Any

from .parallel_tool_scheduler import ParallelPlanError, execute_parallel_plan
from .superagent_worker_capacity import available_worker_slots, worker_ceiling


COGNITIVE_READ_TOOLS = frozenset({"knowledge.search", "runtime.version"})


def parse_parallel_read_request(text: str) -> dict[str, Any] | None:
    """Only a complete JSON plan is executable (never embedded instructions)."""
    try:
        payload = json.loads(str(text or "").strip())
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("action") == "parallel" else None


class _TicketCancelled:
    def __init__(self, service: Any, ticket_id: str):
        self.service = service
        self.ticket_id = ticket_id

    def is_set(self) -> bool:
        return not self.service._ticket_working(self.ticket_id)


class _JournaledReads:
    def __init__(self, service: Any, ticket_id: str, attempt: int,
                 id_to_step: dict[str, int], signatures: dict[str, str]):
        self.service = service
        self.ticket_id = ticket_id
        self.attempt = attempt
        self.id_to_step = id_to_step
        self.signatures = signatures

    def execute(self, name: str, args: dict[str, Any], *, context: Any) -> str:
        # Each requested name+argument signature has a unique journal index.
        signature = json.dumps([name, args], ensure_ascii=False, sort_keys=True)
        number = self.id_to_step[signature]
        journal = self.service._journal
        journal.begin(self.ticket_id, self.attempt, number, name, args)
        try:
            if not self.service._ticket_working(self.ticket_id):
                raise RuntimeError("ticket stopped")
            output = self.service._cognitive_reads.execute(name, args, ticket_id=self.ticket_id)
            journal.finish(self.ticket_id, self.attempt, number, status="success",
                           receipt_id=str(output.get("receipt_id") or ""),
                           result=output.get("result"))
            return json.dumps(output, ensure_ascii=False)
        except Exception:
            try:
                journal.finish(self.ticket_id, self.attempt, number, status="failed")
            except ValueError:
                pass
            raise


def run_superagent_parallel_reads(service: Any, ticket_id: str, attempt: int,
                                  tool_step: int, tasks: Any,
                                  seen_signatures: set[str]) -> list[dict[str, Any]]:
    """Run independent cognitive reads with verifiable receipts and step audit.

    Journal indexes 1..24, 25..48, ... map to each LLM action step. A stopped
    ticket never starts a new tool; failures cannot be reported as successful.
    """
    if not isinstance(tasks, list):
        raise ParallelPlanError("План должен содержать массив tasks")
    mapping: dict[str, int] = {}
    for index, item in enumerate(tasks):
        if isinstance(item, dict):
            try:
                signature = json.dumps(
                    [item.get("tool"), item.get("arguments")],
                    ensure_ascii=False, sort_keys=True,
                )
            except (TypeError, ValueError):
                raise ParallelPlanError("Некорректные аргументы") from None
            mapping[signature] = tool_step * 24 + index + 1
    capacity = available_worker_slots(worker_ceiling())
    if capacity < 1:
        raise ParallelPlanError("Недостаточно свободной памяти для нового параллельного чтения")
    return execute_parallel_plan(
        tasks, _JournaledReads(service, ticket_id, attempt, mapping, {}),
        context=None, seen_signatures=seen_signatures,
        max_workers=capacity, cancel_event=_TicketCancelled(service, ticket_id),
        allowed_tools=COGNITIVE_READ_TOOLS,
    )
