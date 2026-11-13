"""Execute only previously validated, owner-bound durable read steps.

This explicit entry point never interprets text steps or grants shell/network
mutations. Independent reads can run concurrently; SQLite leases serialize
claims across processes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any, Callable

from .durable_plan_store import PlanError, _safe_read_spec
from .superagent_read_evidence import verify_live_receipt
from .superagent_source_reviewer import review_knowledge_sources


def execute_ready_plan_reads(
    service: Any, ticket_id: str, owner_id: str, *,
    max_steps: int = 12, max_workers: int = 4,
    require_auto: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run a bounded batch of safe reads when explicitly requested by owner.

    Completed dependencies can unlock subsequent layers in the same invocation.
    On failure, leave retries to a future invocation; on Stop/Cancel, never
    acknowledge a result with a revoked or expired lease.
    """
    if (type(max_steps) is not int or not 1 <= max_steps <= 24
            or type(max_workers) is not int or not 1 <= max_workers <= 8
            or type(require_auto) is not bool):
        raise PlanError("Invalid read-execution budget")

    bridge = service.plans
    snapshot = bridge.snapshot(ticket_id, owner_id)
    reader = getattr(service, "_cognitive_reads", None)
    if reader is None:
        raise RuntimeError("Trusted read executor is not connected")
    completed = 0
    errors = 0
    stopped = should_stop or (lambda: False)

    def allowed() -> bool:
        if stopped():
            return False
        state = bridge.snapshot(ticket_id, owner_id)
        return state['state'] == 'active' and (not require_auto or state['auto_run'])

    def one(claim: dict[str, Any]) -> tuple[int, int]:
        step_id, token = claim["id"], claim["token"]
        try:
            # Validate AGAIN at execution boundary, even if the SQLite row
            # was migrated, corrupted, or modified since plan creation.
            name, arg_json = _safe_read_spec({
                "tool": claim.get("tool"), "arguments": claim.get("arguments"),
            })
            if not name or json.loads(arg_json) != claim["arguments"]:
                raise PermissionError("Plan step has no approved read action")
            if not allowed():
                return 0, 0
            if not bridge.heartbeat(
                ticket_id, owner_id, step_id, token, lease_seconds=300,
            ):
                return 0, 0

            receipt = reader.execute(name, claim["arguments"], ticket_id=ticket_id)
            if (not isinstance(receipt, dict) or receipt.get("tool") != name
                    or receipt.get("status") != "success"
                    or not isinstance(receipt.get("receipt_id"), str)
                    or not receipt["receipt_id"]):
                raise RuntimeError("Unverified read-tool result")
            # Verify against the trusted executor's live ledger rather than
            # accepting a plausible-looking receipt_id returned by any tool.
            verified = verify_live_receipt(reader, receipt, name)
            evidence = {
                "tool": name, "receipt_id": receipt["receipt_id"],
                "result_sha256": verified["result_sha256"],
                "recorded_at": verified["recorded_at"],
                "verified_receipt": True,
                "result": receipt.get("result"),
            }
            # Independent deterministic review of Knowledge citations, not
            # the model's opinion, before calling the durable step complete.
            if name == "knowledge.search":
                evidence["source_review"] = review_knowledge_sources(receipt.get("result"))
            output = json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if len(output) > 3900:
                # Keep a verifiable digest from the actual receipt ledger.
                # Do not pretend the discarded result is visible to the user.
                del evidence["result"]
                # A review cannot be rechecked once source rows are omitted.
                evidence.pop("source_review", None)
                evidence["result_truncated"] = True
                output = json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if not allowed():
                return 0, 0
            accepted = bridge.complete(
                ticket_id, owner_id, step_id, token, success=True, output=output,
            )
            return (1, 0) if accepted else (0, 0)
        except Exception as exc:
            # No exception details, raw queries, or secret-bearing metadata in
            # user-facing status. Requeue within the store's attempt budget.
            try:
                accepted = bridge.complete(
                    ticket_id, owner_id, step_id, token,
                    success=False, output="read_failed:" + type(exc).__name__,
                )
            except (KeyError, PermissionError, PlanError):
                accepted = False
            return 0, int(accepted)

    while completed + errors < max_steps:
        if not allowed():
            break
        snapshot = bridge.snapshot(ticket_id, owner_id)
        if snapshot["state"] != "active":
            break
        claims = bridge.claim(
            ticket_id, owner_id,
            limit=min(max_workers, max_steps - completed - errors),
            lease_seconds=300, tool_only=True,
        )
        if not claims:
            break
        with ThreadPoolExecutor(max_workers=min(max_workers, len(claims))) as pool:
            results = list(pool.map(one, claims))
        completed += sum(item[0] for item in results)
        errors += sum(item[1] for item in results)
        # Avoid retry storms inside a single chat request.
        if errors or not any(item[0] for item in results):
            break

    final = bridge.snapshot(ticket_id, owner_id)
    return {
        "id": ticket_id, "state": final["state"],
        "total": len(final["steps"]), "completed": completed, "errors": errors,
    }


__all__ = ["execute_ready_plan_reads"]
