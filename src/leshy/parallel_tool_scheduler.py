"""Bounded, dependency-aware parallel execution of explicitly read-only tools.

The scheduler never performs LLM inference and never bypasses ToolRegistry's
own authorization. It is deliberately unsuitable for mutating operations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import threading
from typing import Any


READ_ONLY_TOOLS = frozenset({
    "knowledge.search", "workspace.read", "workspace.list", "agent.status",
    "runtime.version",
})
MAX_TASKS = 24
MAX_WORKERS = 8
MAX_OUTPUT_CHARS = 4000


class ParallelPlanError(ValueError):
    """The requested graph cannot be executed safely."""


def _validate(raw_tasks: Any, *, seen_signatures: set[str],
              allowed_tools: frozenset[str] = READ_ONLY_TOOLS) -> list[dict[str, Any]]:
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= MAX_TASKS:
        raise ParallelPlanError("Для параллельного плана требуется от 1 до 24 задач")
    tasks: list[dict[str, Any]] = []
    ids: set[str] = set()
    signatures: set[str] = set()
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise ParallelPlanError("Каждая задача должна быть JSON-объектом")
        task_id, name, args = raw.get("id"), raw.get("tool"), raw.get("arguments")
        deps = raw.get("depends_on", [])
        if not isinstance(task_id, str) or not (1 <= len(task_id) <= 48) or not task_id.replace("-", "").replace("_", "").isalnum() or task_id in ids:
            raise ParallelPlanError("Недопустимый или повторяющийся id задачи")
        if not isinstance(name, str) or name not in allowed_tools:
            raise ParallelPlanError("Параллельно разрешены только явно проверенные инструменты чтения")
        if not isinstance(args, dict) or not isinstance(deps, list) or len(deps) > MAX_TASKS:
            raise ParallelPlanError("arguments должен быть объектом, depends_on — массивом")
        if not all(isinstance(dep, str) for dep in deps) or len(set(deps)) != len(deps):
            raise ParallelPlanError("Недопустимые зависимости")
        try:
            signature = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
        except (ValueError, TypeError):
            raise ParallelPlanError("Аргументы инструмента должны быть JSON-совместимы") from None
        if len(signature) > 8192 or signature in signatures or signature in seen_signatures:
            raise ParallelPlanError("Повторяющийся или слишком большой инструментальный запрос")
        signatures.add(signature)
        ids.add(task_id)
        tasks.append({"id": task_id, "tool": name, "arguments": args, "depends_on": deps,
                      "signature": signature})
    for task in tasks:
        if task["id"] in task["depends_on"] or any(dep not in ids for dep in task["depends_on"]):
            raise ParallelPlanError("Зависимость указывает на отсутствующую задачу или на себя")
    # Validate all paths BEFORE any tool is called.
    remaining = {item["id"]: set(item["depends_on"]) for item in tasks}
    while remaining:
        ready = {name for name, deps in remaining.items() if not deps}
        if not ready:
            raise ParallelPlanError("План содержит цикл зависимостей")
        for name in ready:
            del remaining[name]
        for deps in remaining.values():
            deps.difference_update(ready)
    return tasks


def execute_parallel_plan(raw_tasks: Any, tools: Any, *, context: Any,
                          seen_signatures: set[str], max_workers: int = 4,
                          cancel_event: threading.Event | None = None,
                          allowed_tools: frozenset[str] = READ_ONLY_TOOLS) -> list[dict[str, Any]]:
    """Run valid independent steps concurrently, returning original plan order.

    Failed dependencies block descendants, but unrelated tasks continue. A
    cancellation prevents *new* calls (already running reads must complete).
    """
    tasks = _validate(raw_tasks, seen_signatures=seen_signatures, allowed_tools=allowed_tools)
    workers = min(MAX_WORKERS, max(1, int(max_workers)), len(tasks))
    completed: dict[str, dict[str, Any]] = {}
    pending = {item["id"]: item for item in tasks}

    def run(item: dict[str, Any]) -> dict[str, Any]:
        if cancel_event is not None and cancel_event.is_set():
            return {"id": item["id"], "status": "cancelled"}
        try:
            output = tools.execute(item["tool"], item["arguments"], context=context)
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, default=str)
            # Treat approvals as failures, never as authorization to proceed.
            try:
                parsed = json.loads(output)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict) and parsed.get("status") == "approval_required":
                return {"id": item["id"], "status": "blocked", "error": "approval_required"}
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n[…результат сокращён…]"
            return {"id": item["id"], "status": "ok", "output": output}
        except Exception as exc:
            return {"id": item["id"], "status": "failed", "error": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="leshy-plan") as pool:
        while pending:
            ready = [item for item in tasks if item["id"] in pending and
                     all(dep in completed for dep in item["depends_on"])]
            if not ready:
                raise ParallelPlanError("План не может быть завершён")
            scheduled = []
            for item in ready:
                pending.pop(item["id"])
                if any(completed[dep]["status"] != "ok" for dep in item["depends_on"]):
                    completed[item["id"]] = {"id": item["id"], "status": "blocked", "error": "dependency_failed"}
                elif cancel_event is not None and cancel_event.is_set():
                    completed[item["id"]] = {"id": item["id"], "status": "cancelled"}
                else:
                    scheduled.append(item)
            futures = {pool.submit(run, item): item for item in scheduled}
            for future in as_completed(futures):
                item = futures[future]
                completed[item["id"]] = future.result()
    seen_signatures.update(item["signature"] for item in tasks)
    return [completed[item["id"]] for item in tasks]
