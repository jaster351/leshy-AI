from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import re
import uuid
from typing import Any

from .cluster_orchestrator import ClusterTask
from .diagnostics import event, problem
from .inference_scheduler import get_inference_scheduler
from .llm import ChatMessage


@dataclass(frozen=True, slots=True)
class AgentRole:
    name: str
    instruction: str


@dataclass(slots=True)
class AgentReport:
    role: str
    node_id: str
    answer: str
    ok: bool = True
    error: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "node_id": self.node_id,
            "answer": self.answer,
            "ok": self.ok,
            "error": self.error,
        }


DEFAULT_ROLES = (
    AgentRole(
        "Исследователь",
        "Проверь фактическую сторону задачи, найди пробелы и неизвестные. Не выдумывай факты. "
        "Отделяй подтверждённое от предположений и дай материал для итогового решения.",
    ),
    AgentRole(
        "Архитектор",
        "Разложи задачу на части, предложи практическое решение и зависимости. Учитывай отказоустойчивость, "
        "простоту сопровождения и уже существующие компоненты Лешего.",
    ),
    AgentRole(
        "Критик",
        "Найди слабые места, противоречия, риски безопасности и сценарии отказа. Не спорь ради спора: "
        "предложи конкретные исправления и критерии проверки.",
    ),
    AgentRole(
        "Оптимизатор",
        "Ищи более быстрый, дешёвый по ресурсам и автономный способ выполнить задачу без потери качества. "
        "Отдельно отмечай, что можно распараллелить между узлами кластера.",
    ),
)


class MultiAgentOrchestrator:
    """Deep-Agents/LangGraph-подобный оркестратор поверх собственных компонентов Лешего.

    Подагенты не имеют независимой долговременной личности: они получают отдельную
    роль, выполняют ограниченную подзадачу и возвращают отчёт координатору. При
    наличии распределённого кластера роли отправляются на разные компьютеры и
    выполняются параллельно. Финальный ответ всегда собирает origin-узел.
    """

    def __init__(self, app):
        self.app = app
        self.roles = DEFAULT_ROLES

    @staticmethod
    def parse_request(text: str) -> str | None:
        value = " ".join(str(text or "").split()).strip()
        low = value.lower()
        prefixes = ("/agents ", "/агенты ", "/multiagent ")
        for prefix in prefixes:
            if low.startswith(prefix):
                return value[len(prefix):].strip()
        markers = (
            "используй команду агентов",
            "подключи несколько агентов",
            "разбери несколькими агентами",
            "проанализируй несколькими агентами",
            "запусти агентов для",
            "запусти команду агентов",
        )
        if any(marker in low for marker in markers):
            cleaned = value
            for marker in markers:
                cleaned = re.sub(re.escape(marker), "", cleaned, flags=re.I)
            return cleaned.strip(" :,-—") or value
        return None

    def _shared_context(self, owner_id: str, task: str) -> str:
        blocks: list[str] = []
        memory_engine = getattr(self.app, "intelligent_memory", None)
        if memory_engine is not None:
            try:
                memory = memory_engine.context(owner_id, task, limit=5)
                if memory:
                    blocks.append("Релевантная память пользователя:\n" + memory)
            except Exception:
                pass
        graph = getattr(self.app, "knowledge_graph", None)
        if graph is not None:
            try:
                graph_context = graph.context(owner_id, task, limit=6)
                if graph_context:
                    blocks.append("Связанные знания GraphRAG:\n" + graph_context)
            except Exception:
                pass
        return "\n\n".join(blocks)

    def _local_role(self, role: AgentRole, task: str, context: str, *, priority: int) -> AgentReport:
        system = (
            f"Ты подагент Лешего с ролью «{role.name}». {role.instruction} "
            "Верни только свой рабочий отчёт для координатора. Не притворяйся, что выполнил действие, которого не выполнял."
        )
        prompt = task if not context else task + "\n\nКонтекст:\n" + context
        scheduler = get_inference_scheduler(self.app)
        try:
            with scheduler.slot(source=f"subagent:{role.name}", priority=priority):
                answer = self.app.llm.chat([ChatMessage("system", system), ChatMessage("user", prompt)])
            return AgentReport(role=role.name, node_id="local", answer=str(answer or "").strip())
        except Exception as exc:
            return AgentReport(role=role.name, node_id="local", answer="", ok=False, error=str(exc))

    def _cluster_role(
        self,
        cluster,
        node,
        role: AgentRole,
        task: str,
        context: str,
        *,
        owner_id: str,
        session_id: str,
        request_id: str,
        priority: int,
    ) -> AgentReport:
        system = (
            f"Ты подагент Лешего с ролью «{role.name}». {role.instruction} "
            "Верни только рабочий отчёт координатору. Не выдумывай результаты инструментов."
        )
        prompt = task if not context else task + "\n\nКонтекст:\n" + context
        subtask = ClusterTask.create(
            origin_node_id=cluster.identity.node_id,
            reply_to=cluster.local_node.endpoint,
            owner_id=owner_id,
            session_id=session_id,
            request_id=request_id,
            priority=priority,
            kind="agent.prompt",
            payload={"role": role.name, "system": system, "prompt": prompt},
            ttl_seconds=240.0,
        )
        try:
            if node.node_id == cluster.identity.node_id:
                result = cluster._execute_local(subtask)
            else:
                result = cluster.transport.submit(node, subtask)
            if not result.ok:
                return AgentReport(role=role.name, node_id=node.node_id, answer="", ok=False, error=result.error)
            return AgentReport(
                role=role.name,
                node_id=result.executor_node_id or node.node_id,
                answer=str(result.payload.get("answer") or "").strip(),
            )
        except Exception as exc:
            problem("multi_agent_cluster_role_failed", str(exc), role=role.name, node_id=node.node_id)
            return AgentReport(role=role.name, node_id=node.node_id, answer="", ok=False, error=str(exc))

    def _run_roles(
        self,
        task: str,
        *,
        owner_id: str,
        session_id: str,
        request_id: str,
        priority: int,
    ) -> list[AgentReport]:
        context = self._shared_context(owner_id, task)
        cluster = getattr(self.app, "distributed_cluster", None)
        nodes = []
        if cluster is not None:
            try:
                nodes = [node for node in cluster.refresh() if node.accepting_tasks]
            except Exception as exc:
                problem("multi_agent_cluster_refresh_failed", str(exc))
                nodes = []

        # Один компьютер не ускорится от параллельных обращений к одному inference-slot,
        # поэтому локально роли выполняются последовательно. При двух и более узлах
        # роли раскладываются по компьютерам и запускаются одновременно.
        if len(nodes) < 2:
            return [self._local_role(role, task, context, priority=priority) for role in self.roles]

        # One request lane per physical node. Independent nodes execute in
        # parallel, while each node receives only one inference at a time.
        # A task's source and owner/session remain bound to the original call.
        unique_nodes = list({node.node_id: node for node in nodes}.values())
        if len(unique_nodes) < 2:
            return [self._local_role(role, task, context, priority=priority) for role in self.roles]
        lanes: dict[str, tuple[Any, list[AgentRole]]] = {
            node.node_id: (node, []) for node in unique_nodes
        }
        for index, role in enumerate(self.roles):
            lanes[unique_nodes[index % len(unique_nodes)].node_id][1].append(role)

        def run_lane(node, roles: list[AgentRole]) -> list[AgentReport]:
            results: list[AgentReport] = []
            for role in roles:
                try:
                    results.append(self._cluster_role(
                        cluster, node, role, task, context,
                        owner_id=owner_id, session_id=session_id,
                        request_id=request_id, priority=priority,
                    ))
                except Exception as exc:
                    problem("multi_agent_role_dispatch_failed", str(exc),
                            role=role.name, node_id=node.node_id)
                    results.append(AgentReport(
                        role=role.name, node_id=node.node_id,
                        answer="", ok=False, error=str(exc),
                    ))
            return results

        reports: list[AgentReport] = []
        with ThreadPoolExecutor(
            max_workers=min(len(lanes), 8), thread_name_prefix="leshy-agent-node"
        ) as pool:
            futures = {
                pool.submit(run_lane, node, roles): (node.node_id, roles)
                for node, roles in lanes.values()
                if roles
            }
            for future in as_completed(futures):
                try:
                    reports.extend(future.result())
                except Exception as exc:
                    node_id, roles = futures[future]
                    problem("multi_agent_node_lane_failed", str(exc), node_id=node_id)
                    reports.extend(
                        AgentReport(role=role.name, node_id=node_id,
                                    answer="", ok=False, error=str(exc))
                        for role in roles
                    )
        order = {role.name: index for index, role in enumerate(self.roles)}
        reports.sort(key=lambda item: order.get(item.role, 999))
        event("multi_agent_lanes_finished", request_id=request_id,
              lanes=len(futures), roles=len(reports),
              failed=sum(not report.ok for report in reports))
        return reports

    def run(
        self,
        task: str,
        *,
        owner_id: str,
        session_id: str,
        request_id: str | None = None,
        priority: int = 70,
    ) -> dict[str, Any]:
        task = " ".join(str(task or "").split()).strip()
        if not task:
            raise ValueError("Задача для команды агентов пустая")
        rid = str(request_id or uuid.uuid4())
        event("multi_agent_started", request_id=rid, owner_id=owner_id, session_id=session_id)
        reports = self._run_roles(
            task,
            owner_id=owner_id,
            session_id=session_id,
            request_id=rid,
            priority=priority,
        )
        usable = [report for report in reports if report.ok and report.answer]
        if not usable:
            details = "; ".join(report.error for report in reports if report.error) or "подагенты не вернули ответ"
            raise RuntimeError("Команда агентов не смогла выполнить задачу: " + details)

        joined = "\n\n".join(
            f"### {report.role} (узел {report.node_id})\n{report.answer}"
            for report in usable
        )
        synthesis_system = (
            "Ты координатор команды подагентов Лешего. Сформируй один законченный ответ пользователю на русском языке. "
            "Устрани дубли, разреши противоречия, не добавляй неподтверждённых фактов. Если отчёты расходятся и проверить нельзя, "
            "явно укажи неопределённость. Не рассказывай о внутреннем chain-of-thought; используй только выводы отчётов."
        )
        synthesis_prompt = f"Задача пользователя:\n{task}\n\nОтчёты подагентов:\n{joined}"
        scheduler = get_inference_scheduler(self.app)
        with scheduler.slot(source="multi-agent:synthesis", priority=priority):
            answer = self.app.llm.chat([
                ChatMessage("system", synthesis_system),
                ChatMessage("user", synthesis_prompt),
            ])
        answer = str(answer or "").strip()
        if not answer:
            answer = "Не удалось сформировать итоговый ответ команды агентов."
        cluster_nodes = sorted({report.node_id for report in usable if report.node_id})
        event(
            "multi_agent_finished",
            request_id=rid,
            reports=len(usable),
            nodes=len(cluster_nodes),
            answer_chars=len(answer),
        )
        return {
            "answer": answer,
            "reports": [report.public() for report in reports],
            "request_id": rid,
            "nodes": cluster_nodes,
            "distributed": len(cluster_nodes) > 1,
        }

    def status(self) -> dict[str, Any]:
        cluster = getattr(self.app, "distributed_cluster", None)
        peer_count = 1
        if cluster is not None:
            try:
                peer_count = int(cluster.status().get("peer_count") or 1)
            except Exception:
                peer_count = 1
        return {
            "enabled": True,
            "roles": [role.name for role in self.roles],
            "cluster_peer_count": peer_count,
            "distributed_when_possible": True,
        }


__all__ = ["AgentRole", "AgentReport", "MultiAgentOrchestrator", "DEFAULT_ROLES"]
