"""Ограниченный доступ Cognitive Core к проверяемым инструментам чтения.

Никакие ответы модели не могут зарегистрировать квитанцию. Реестр получает
квитанцию только после возврата результата настоящего метода приложения.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .diagnostics import event
from .verified_tool_receipts import VerifiedToolReceipts
from .version import VERSION


_TOOL_NAMES = ("knowledge.search", "runtime.version")
_FENCE = re.compile(r"^\s*```(?:json)?\s*(\{.*\})\s*```\s*$", re.IGNORECASE | re.DOTALL)


def parse_tool_request(response: str) -> dict[str, Any] | None:
    """Распознавать только полный JSON; не исполнять команды из свободного текста."""
    text = str(response or "").strip()
    fenced = _FENCE.fullmatch(text)
    if fenced is not None:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("action") != "tool":
        return None
    return value


class CognitiveReadExecutor:
    """Явный allow-list без shell, ОС, сети, записи файлов и MCP-mutations."""

    def __init__(self, app, *, receipts: VerifiedToolReceipts | None = None):
        self.app = app
        self.receipts = receipts if receipts is not None else VerifiedToolReceipts()

    @staticmethod
    def names() -> tuple[str, ...]:
        return _TOOL_NAMES

    def execute(self, name: str, arguments: dict[str, Any], *, ticket_id: str) -> dict[str, Any]:
        tool = str(name or "")
        if tool not in _TOOL_NAMES:
            raise PermissionError("Инструмент не входит в разрешённый список Cognitive Core")
        if not isinstance(arguments, dict):
            raise ValueError("Аргументы инструмента должны быть JSON-объектом")
        if tool == "runtime.version":
            if arguments:
                raise ValueError("runtime.version не принимает аргументы")
            result: dict[str, Any] = {"version": VERSION}
        else:
            query = " ".join(str(arguments.get("query") or "").split())
            if not (3 <= len(query) <= 300):
                raise ValueError("Для knowledge.search требуется запрос от 3 до 300 символов")
            knowledge = getattr(self.app, "knowledge", None)
            if knowledge is None:
                raise RuntimeError("База доверенных знаний недоступна")
            rows = knowledge.search(query, trusted_only=True, limit=5)
            # Исключаем необязательные поля БД и секретные метаданные из контекста LLM.
            items = [
                {
                    "topic": str(item.get("topic") or "")[:180],
                    "claim": str(item.get("claim") or "")[:1000],
                    "source_url": str(item.get("source_url") or "")[:500],
                }
                for item in rows[:5] if isinstance(item, dict)
            ]
            result = {"items": items, "count": len(items)}
        # Квитанцию создаёт исполненный код, не LLM и не внешнее содержимое.
        receipt = self.receipts.record(tool, "success", result)
        event(
            "cognitive_read_tool_executed",
            ticket_id=str(ticket_id)[:100],
            tool=tool,
            receipt_id=receipt.receipt_id,
            result_count=len(result.get("items", [])),
        )
        return {
            "tool": tool,
            "status": "success",
            "receipt_id": receipt.receipt_id,
            "result": result,
        }


__all__ = ["CognitiveReadExecutor", "parse_tool_request"]
