"""Private, bounded summaries of actual SuperAgent DAG read results.

Evidence never includes raw Knowledge claims, queries, source URLs, credentials
or tool arguments. An in-process receipt attests execution, NOT truthfulness of
source material or integrity of a potentially modified SQLite database.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOOLS = frozenset({"runtime.version", "runtime.platform", "knowledge.search"})


def read_result_digest(result: Any) -> str:
    """Use the same canonical encoding as VerifiedToolReceipts.record."""
    content = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
    return sha256(content.encode("utf-8")).hexdigest()


def verify_live_receipt(reader: Any, response: dict[str, Any], tool: str) -> dict[str, str]:
    """Fail closed if receipt ID does not belong to the local execution ledger."""
    ledger = getattr(reader, "receipts", None)
    if ledger is None or not callable(getattr(ledger, "verify", None)):
        raise RuntimeError("Tool receipt ledger unavailable")
    receipt_id = response.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise RuntimeError("Tool did not provide a receipt")
    record = ledger.verify(receipt_id, tool=tool)
    if (not isinstance(record, dict) or record.get("verified") is not True
            or record.get("status") != "success"
            or record.get("tool") != tool):
        raise RuntimeError("Tool receipt not verified")
    result_hash = read_result_digest(response.get("result"))
    if record.get("result_sha256") != result_hash:
        raise RuntimeError("Tool receipt result digest mismatch")
    timestamp = record.get("timestamp_utc")
    if not isinstance(timestamp, str) or len(timestamp) > 64 or not timestamp:
        raise RuntimeError("Tool receipt timestamp invalid")
    return {
        "result_sha256": result_hash,
        "recorded_at": timestamp,
    }


def summarize_plan_evidence(step: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize a stored receipt for its already-authorized owner.

    Bad, old, and tampered records never receive a verified UI label.
    """
    if step.get("state") != "done" or step.get("tool") not in _TOOLS:
        return None
    output = step.get("output")
    if not isinstance(output, str) or len(output) > 4096:
        return {"status": "unavailable", "description": "Сведения о выполнении недоступны"}
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return {"status": "unavailable", "description": "Сведения о выполнении повреждены"}
    if not isinstance(data, dict) or data.get("tool") != step["tool"]:
        return {"status": "unavailable", "description": "Сведения о выполнении не совпадают с этапом"}
    receipt = data.get("receipt_id")
    if not isinstance(receipt, str) or len(receipt) != 32 or not re.fullmatch("[0-9a-f]{32}", receipt):
        # Older tests, legacy imports and externally written claims must not
        # be represented as verified tool executions.
        return {"status": "unavailable", "description": "Нет подтверждённой квитанции"}
    digest = data.get("result_sha256")
    is_new = data.get("verified_receipt") is True and isinstance(digest, str) and bool(_SHA256.fullmatch(digest))
    if not is_new:
        return {"status": "legacy",
                "description": "Квитанция прежнего формата; проверка выполнения недоступна"}
    truncated = data.get("result_truncated") is True
    result = data.get("result")
    if truncated:
        if "result" in data:
            return {"status": "unavailable", "description": "Некорректная квитанция"}
    elif read_result_digest(result) != digest:
        return {"status": "unavailable", "description": "Сохранённый результат не совпадает с квитанцией"}
    result_summary = None
    if not truncated and isinstance(result, dict):
        tool = step["tool"]
        if tool == "runtime.version" and isinstance(result.get("version"), str):
            result_summary = "Версия: " + result["version"][:60]
        elif tool == "runtime.platform":
            system = result.get("system")
            if isinstance(system, str):
                result_summary = "Система Лешего: " + system[:80]
        elif tool == "knowledge.search":
            count = result.get("count")
            if type(count) is int and 0 <= count <= 5:
                result_summary = f"Найдено проверенных записей: {count}"
    return {
        "status": "recorded",
        "description": "Исполнение подтверждено локальной квитанцией при выполнении",
        "receipt_id": receipt,
        "result_sha256": digest,
        "recorded_at": str(data.get("recorded_at") or "")[:64],
        "result_truncated": truncated,
        "summary": result_summary,
    }


__all__ = ["read_result_digest", "verify_live_receipt", "summarize_plan_evidence"]
