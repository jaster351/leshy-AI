"""Human-invoked durable plan commands for the existing SuperAgent routing."""
from __future__ import annotations

import json
from typing import Any


def execute_plan_command(service: Any, action: str, ticket_id: str,
                         owner_id: str, argument: str = "") -> str:
    if action == "plan_create":
        data = json.loads(argument)
        steps = data.get("steps") if isinstance(data, dict) else data
        if not isinstance(steps, list):
            raise ValueError("План должен содержать массив steps")
        result = service.plans.create(ticket_id, owner_id, steps)
        return (f"План создан для {result['id']}: {len(result['steps'])} этапов. "
                "Этапы сохранены, но не запущены автоматически. "
                f"Состояние: /agent plan status {result['id']}")
    if action == "plan_status":
        result = service.plans.snapshot(ticket_id, owner_id)
        steps = result["steps"]
        shown = steps[:64]  # Keep chat output bounded; never hide the total.
        lines = [f"План {result['id']}: {result['state']} — {len(steps)} этапов"]
        lines.extend(f"- {step['id']}: {step['state']} ({step['title'][:100]})"
                     for step in shown)
        if len(steps) > len(shown):
            summary: dict[str, int] = {}
            for step in steps:
                summary[step["state"]] = summary.get(step["state"], 0) + 1
            counts = ", ".join(f"{state}: {count}" for state, count in sorted(summary.items()))
            lines.append(f"Показано {len(shown)} из {len(steps)}. Все статусы: {counts}")
        return "\n".join(lines)
    controls = {"plan_pause": "pause", "plan_resume": "resume", "plan_cancel": "cancel"}
    if action in controls:
        result = service.plans.control(ticket_id, owner_id, controls[action])
        return f"План {result['id']}: {result['state']}"
    raise ValueError("Неизвестная команда планировщика")
