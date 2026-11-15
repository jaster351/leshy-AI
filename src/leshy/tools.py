from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from .approvals import ApprovalStore
from .knowledge import KnowledgeBase
from .memory import MemoryStore
from .permissions import PermissionPolicy
from .research import ResearchEngine
from .storage import Storage
from .version import VERSION
from .workspace import ShellExecutor, Workspace


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionContext:
    owner_id: str = "standalone"
    session_id: str | None = None


class ToolRegistry:
    def __init__(self, policy: PermissionPolicy, approvals: ApprovalStore | None = None):
        self.policy = policy
        self.approvals = approvals
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def register(self, spec: ToolSpec, handler: Callable[[dict[str, Any]], Any]) -> None:
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def has(self, name: str) -> bool:
        return name in self._handlers

    def execute(self, name: str, arguments: dict[str, Any], *, context: ToolExecutionContext | None = None) -> str:
        permission = self.policy.require(name)
        handler = self._handlers.get(name)
        if not handler:
            raise KeyError(f"Неизвестный инструмент: {name}")

        execution_context = context or ToolExecutionContext()
        if permission.approval_required:
            if self.approvals is None:
                raise RuntimeError(f"Инструмент {name} требует Human Approval Gate, но gate не настроен")
            consumed = self.approvals.consume(
                owner_id=execution_context.owner_id,
                tool_name=name,
                arguments=arguments,
            )
            if consumed is None:
                pending = self.approvals.request(
                    owner_id=execution_context.owner_id,
                    session_id=execution_context.session_id,
                    tool_name=name,
                    arguments=arguments,
                    risk=permission.risk.value,
                )
                return json.dumps({
                    "status": "approval_required",
                    "approval_id": pending.id,
                    "tool": name,
                    "risk": permission.risk.value,
                    "expires_at": pending.expires_at,
                    "message": "Действие не выполнено. Требуется явное подтверждение человека.",
                }, ensure_ascii=False)

        return json.dumps(handler(arguments), ensure_ascii=False, default=str)


def build_default_tools(
    storage: Storage,
    knowledge: KnowledgeBase,
    memory: MemoryStore,
    research: ResearchEngine,
    workspace: Workspace,
    policy: PermissionPolicy | None = None,
    approvals: ApprovalStore | None = None,
    *,
    enable_shell: bool = False,
) -> ToolRegistry:
    registry = ToolRegistry(policy or PermissionPolicy.safe_default(allow_exec=enable_shell), approvals=approvals)

    registry.register(
        ToolSpec("knowledge.search", "Найти только доверенные знания Лешего.", {"query": "string"}),
        lambda a: knowledge.search(str(a.get("query", "")), trusted_only=True, limit=8),
    )
    registry.register(
        ToolSpec(
            "memory.remember",
            "Сохранить полезный обобщённый неперсональный опыт. Не использовать для секретов.",
            {"content": "string", "importance": "0..1"},
        ),
        lambda a: {
            "memory_id": memory.remember(
                str(a.get("content", "")),
                kind="experience",
                importance=float(a.get("importance", 0.6)),
                source="agent",
                owner_id="shared",
            )
        },
    )
    registry.register(
        ToolSpec(
            "research.search",
            "Быстро найти актуальные сведения в интернете. Результаты сразу сохраняются как candidate-знания, глубокая проверка ставится в очередь.",
            {"query": "string"},
        ),
        lambda a: research.live_query(str(a.get("query", "")), reason="запрос планировщика"),
    )
    registry.register(
        ToolSpec(
            "research.deep",
            "Выполнить полное интернет-исследование с чтением источников и верификацией. Использовать для фонового обучения, а не для быстрого пользовательского ответа.",
            {"query": "string"},
        ),
        lambda a: research.run_query(str(a.get("query", "")), reason="глубокое исследование"),
    )
    registry.register(
        ToolSpec("workspace.read", "Прочитать UTF-8 файл внутри рабочего пространства.", {"path": "string"}),
        lambda a: {"content": workspace.read(str(a.get("path", "")))},
    )
    registry.register(
        ToolSpec("workspace.list", "Показать файлы каталога внутри рабочего пространства.", {"path": "string"}),
        lambda a: {"items": workspace.list(str(a.get("path", ".")))},
    )
    registry.register(
        ToolSpec(
            "workspace.write",
            "Атомарно записать текстовый файл внутри рабочего пространства. Всегда требует подтверждения человека.",
            {"path": "string", "content": "string"},
        ),
        lambda a: workspace.write(str(a.get("path", "")), str(a.get("content", ""))),
    )
    if enable_shell:
        shell = ShellExecutor(workspace)
        registry.register(
            ToolSpec(
                "shell.exec",
                "Выполнить команду из жёсткого allow-list. Всегда требует подтверждения человека.",
                {"command": "string", "timeout": "1..300"},
            ),
            lambda a: shell.run(str(a.get("command", "")), int(a.get("timeout", 90))),
        )

    def status(_):
        counts = knowledge.counts()
        with storage.connection() as conn:
            return {
                "version": VERSION,
                "sessions": conn.execute("SELECT count(*) n FROM sessions").fetchone()["n"],
                "knowledge_total": counts["total"],
                "candidate_knowledge": counts["candidate"],
                "trusted_knowledge": counts["trusted"],
                "stale_knowledge": counts["stale"],
                "conflict_knowledge": counts["conflict"],
                "queued_research": conn.execute("SELECT count(*) n FROM research_tasks WHERE status='queued'").fetchone()["n"],
                "running_research": conn.execute("SELECT count(*) n FROM research_tasks WHERE status='running'").fetchone()["n"],
                "pending_approvals": conn.execute("SELECT count(*) n FROM tool_approvals WHERE status='pending'").fetchone()["n"],
            }

    registry.register(ToolSpec("agent.status", "Получить техническое состояние Лешего.", {}), status)
    return registry
