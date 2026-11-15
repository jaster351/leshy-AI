"""Verified, privacy-preserving receipts for actual tool executions.

A receipt is issued by the executor, never by the language model.
This module does not claim that a source's *content* is true: it attests only
that a named tool was actually invoked and returned a result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import threading
import uuid
from typing import Any


@dataclass(frozen=True)
class ToolReceipt:
    receipt_id: str
    tool: str
    status: str
    timestamp_utc: str
    result_sha256: str


class VerifiedToolReceipts:
    """Thread-safe bounded in-process receipt ledger, without arguments or output."""

    def __init__(self, max_entries: int = 2048):
        self._max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._receipts: dict[str, ToolReceipt] = {}

    def record(self, tool: str, status: str, result: Any) -> ToolReceipt:
        if status not in {"success", "error", "approval_required"}:
            raise ValueError("invalid tool status")
        encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        receipt = ToolReceipt(
            receipt_id=uuid.uuid4().hex,
            tool=tool,
            status=status,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            result_sha256=hashlib.sha256(encoded).hexdigest(),
        )
        with self._lock:
            self._receipts[receipt.receipt_id] = receipt
            while len(self._receipts) > self._max_entries:
                del self._receipts[next(iter(self._receipts))]
        return receipt

    def verify(self, receipt_id: str, *, tool: str | None = None) -> dict[str, Any]:
        with self._lock:
            receipt = self._receipts.get(receipt_id)
        if receipt is None or (tool is not None and receipt.tool != tool):
            return {"verified": False}
        return {"verified": True, **asdict(receipt)}
