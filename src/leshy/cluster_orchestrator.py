from __future__ import annotations

from dataclasses import dataclass, field
import math
import threading
import time
import uuid
from typing import Any, Callable, Protocol

from .diagnostics import event


COOPERATIVE_MODE = "cooperative"
MODEL_PARALLEL_MODE = "model_parallel"


@dataclass(slots=True)
class ClusterNode:
    node_id: str
    endpoint: str
    cpu_threads: int
    gpu_names: tuple[str, ...] = ()
    npu_names: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    memory_total_gb: float = 0.0
    memory_available_gb: float = 0.0
    queue_depth: int = 0
    load: float = 0.0
    accepting_tasks: bool = True
    last_seen: float = field(default_factory=time.time)

    def supports(self, task: "ClusterTask") -> bool:
        required = {str(item).lower() for item in task.requirements}
        if "gpu" in required and not self.gpu_names:
            return False
        if "npu" in required and not self.npu_names:
            return False
        if "cpu" in required and self.cpu_threads < 1:
            return False
        for item in required:
            if not item.startswith("ram>="):
                continue
            try:
                minimum = float(item.split("=", 1)[1])
                available = float(self.memory_available_gb)
            except (TypeError, ValueError):
                return False
            # Unknown/NaN RAM must never satisfy an explicit requirement.
            if (
                not math.isfinite(minimum) or minimum <= 0
                or not math.isfinite(available) or available < minimum
            ):
                return False
        if task.model and task.model not in self.models:
            return False
        if task.mode == MODEL_PARALLEL_MODE and task.model and task.model not in self.models:
            return False
        return bool(self.accepting_tasks)


@dataclass(slots=True)
class ClusterTask:
    task_id: str
    origin_node_id: str
    reply_to: str
    owner_id: str
    session_id: str
    request_id: str
    priority: int
    kind: str
    payload: dict[str, Any]
    mode: str = COOPERATIVE_MODE
    model: str = ""
    requirements: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)
    ttl_seconds: float = 120.0

    @classmethod
    def create(
        cls,
        *,
        origin_node_id: str,
        reply_to: str,
        owner_id: str,
        session_id: str,
        request_id: str,
        priority: int,
        kind: str,
        payload: dict[str, Any],
        mode: str = COOPERATIVE_MODE,
        model: str = "",
        requirements: tuple[str, ...] = (),
        ttl_seconds: float = 120.0,
    ) -> "ClusterTask":
        return cls(
            task_id=str(uuid.uuid4()),
            origin_node_id=str(origin_node_id),
            reply_to=str(reply_to),
            owner_id=str(owner_id),
            session_id=str(session_id),
            request_id=str(request_id),
            priority=int(priority),
            kind=str(kind),
            payload=dict(payload),
            mode=str(mode or COOPERATIVE_MODE),
            model=str(model),
            requirements=tuple(str(item).lower() for item in requirements),
            ttl_seconds=max(1.0, float(ttl_seconds)),
        )

    def expired(self) -> bool:
        return time.time() > self.created_at + self.ttl_seconds


@dataclass(slots=True)
class ClusterResult:
    task_id: str
    origin_node_id: str
    executor_node_id: str
    request_id: str
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    finished_at: float = field(default_factory=time.time)


class PeerDiscovery(Protocol):
    """Tracker/DHT/mDNS/LAN discovery реализуют один и тот же контракт."""

    def announce(self, node: ClusterNode) -> None: ...
    def peers(self) -> list[ClusterNode]: ...


class ClusterTransport(Protocol):
    """HTTP сейчас, RabbitMQ/NATS/Redis Streams позже — контракт не меняется."""

    def submit(self, node: ClusterNode, task: ClusterTask) -> ClusterResult: ...


@dataclass(slots=True)
class RabbitMQTopology:
    """Зафиксированный контракт будущего RabbitMQ транспорта."""

    task_exchange: str = "leshy.cluster.tasks"
    result_exchange: str = "leshy.cluster.results"
    node_exchange: str = "leshy.cluster.nodes"
    dead_letter_exchange: str = "leshy.cluster.dead"

    @staticmethod
    def task_routing_key(task: ClusterTask) -> str:
        return f"priority.{int(task.priority):03d}.{task.kind}"

    @staticmethod
    def result_routing_key(task: ClusterTask) -> str:
        return "node." + task.origin_node_id


def cluster_public_summary(status: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    """Safe aggregate for all users; never publish peer addresses or node identities."""
    if not enabled:
        return {
            "enabled": False, "scope": "trusted_lan", "node_count": 0,
            "busy_nodes": 0, "available_nodes": 0, "cpu_threads": 0,
            "ram_total_gb": 0.0, "gpu_count": 0, "npu_count": 0,
        }
    peers = list(status.get("peers") or [])
    inflight = dict(status.get("inflight_by_node") or {})
    busy = sum(
        int(
            int(peer.get("queue_depth") or 0) + int(inflight.get(peer.get("node_id"), 0)) > 0
        )
        for peer in peers
    )
    return {
        "enabled": True,
        "scope": "trusted_lan",
        "node_count": len(peers),
        "busy_nodes": busy,
        "available_nodes": max(0, len(peers) - busy),
        "cpu_threads": sum(int(peer.get("cpu_threads") or 0) for peer in peers),
        "ram_total_gb": round(sum(float(peer.get("memory_total_gb") or 0) for peer in peers), 1),
        "gpu_count": sum(len(peer.get("gpu_names") or ()) for peer in peers),
        "npu_count": sum(len(peer.get("npu_names") or ()) for peer in peers),
    }


class ClusterOrchestrator:
    """Распределяет полезные подзадачи между компьютерами Лешего.

    В cooperative-режиме origin/leader формирует итоговый пользовательский ответ,
    а остальные компьютеры выполняют независимые полезные подзадачи: инференс
    подагентов, поиск, RAG/GraphRAG, embeddings, документы, ASR/TTS и фоновые
    вычисления. Никакого Proof-of-Work или монетизации здесь нет.

    model_parallel зарезервирован для совместимого runtime, который умеет
    действительно делить одну модель между несколькими узлами. Независимые LLM
    никогда не склеиваются по половине токенов одного ответа.
    """

    def __init__(
        self,
        local_node: ClusterNode,
        *,
        discovery: PeerDiscovery | None = None,
        transport: ClusterTransport | None = None,
        peer_ttl_seconds: float = 45.0,
    ):
        self.local_node = local_node
        self.discovery = discovery
        self.transport = transport
        self.peer_ttl_seconds = max(5.0, float(peer_ttl_seconds))
        self._lock = threading.RLock()
        self._peers: dict[str, ClusterNode] = {local_node.node_id: local_node}
        # Unfinished placements from this origin must count before remote heartbeat catches up.
        self._inflight: dict[str, int] = {}

    def announce_and_refresh(self) -> list[ClusterNode]:
        if self.discovery is None:
            return self.peers()
        self.discovery.announce(self.local_node)
        now = time.time()
        discovered = self.discovery.peers()
        with self._lock:
            self._peers[self.local_node.node_id] = self.local_node
            for peer in discovered:
                if peer.node_id and now - peer.last_seen <= self.peer_ttl_seconds:
                    self._peers[peer.node_id] = peer
            stale = [
                node_id for node_id, peer in self._peers.items()
                if node_id != self.local_node.node_id and now - peer.last_seen > self.peer_ttl_seconds
            ]
            for node_id in stale:
                self._peers.pop(node_id, None)
        event("cluster_peers_refreshed", peer_count=len(self._peers))
        return self.peers()

    def forget_remote_peers(self) -> None:
        """После отключения discovery не выдавать устаревшие узлы за активные."""
        with self._lock:
            self._peers = {self.local_node.node_id: self.local_node}

    def register_peer(self, peer: ClusterNode) -> None:
        with self._lock:
            self._peers[peer.node_id] = peer

    def peers(self) -> list[ClusterNode]:
        with self._lock:
            return list(self._peers.values())

    @staticmethod
    def _score(node: ClusterNode, task: ClusterTask, *, inflight: int = 0) -> tuple[float, str]:
        required = set(task.requirements)
        accelerator_bonus = 0.0
        if node.gpu_names:
            accelerator_bonus -= 18.0 if task.kind in {"agent.prompt", "llm.chat", "embedding.batch"} else 6.0
        if node.npu_names:
            accelerator_bonus -= 10.0 if task.kind in {"embedding.batch", "vision.embed"} else 3.0
        if "gpu" in required and node.gpu_names:
            accelerator_bonus -= 25.0
        if "npu" in required and node.npu_names:
            accelerator_bonus -= 20.0

        memory_bonus = 0.0
        if node.memory_available_gb > 0:
            memory_bonus = -min(node.memory_available_gb, 64.0) * 0.35

        cpu_bonus = -min(max(0, node.cpu_threads), 64) * 0.10
        score = (
            (node.queue_depth + inflight) * 125.0
            + max(0.0, node.load) * 100.0
            + accelerator_bonus
            + memory_bonus
            + cpu_bonus
        )
        return score, node.node_id

    def _choose_executor_locked(self, task: ClusterTask) -> ClusterNode:
        candidates = [node for node in self._peers.values() if node.supports(task)]
        if not candidates:
            if task.requirements or task.model or task.mode == MODEL_PARALLEL_MODE:
                raise RuntimeError("Нет доступного узла, удовлетворяющего требованиям задачи")
            return self.local_node
        # An idle compatible node takes precedence over another node's busy inference slot.
        # This prevents a fast GPU from becoming the destination for every parallel task.
        if task.kind == "agent.prompt":
            idle = [
                node for node in candidates
                if node.queue_depth + self._inflight.get(node.node_id, 0) == 0
            ]
            if idle:
                candidates = idle
        return min(
            candidates,
            key=lambda node: self._score(
                node, task, inflight=self._inflight.get(node.node_id, 0)
            ),
        )

    def choose_executor(self, task: ClusterTask) -> ClusterNode:
        with self._lock:
            return self._choose_executor_locked(task)

    def execute_or_delegate(
        self,
        task: ClusterTask,
        local_handler: Callable[[ClusterTask], ClusterResult],
    ) -> ClusterResult:
        if task.expired():
            return ClusterResult(
                task_id=task.task_id,
                origin_node_id=task.origin_node_id,
                executor_node_id=self.local_node.node_id,
                request_id=task.request_id,
                ok=False,
                error="Срок жизни кластерной задачи истёк",
            )

        # Selection and reservation are atomic: concurrent requests from this
        # origin must see each other's unfinished placements immediately.
        with self._lock:
            node = self._choose_executor_locked(task)
            self._inflight[node.node_id] = self._inflight.get(node.node_id, 0) + 1
        try:
            if node.node_id == self.local_node.node_id or self.transport is None:
                event("cluster_task_local", task_id=task.task_id, kind=task.kind, priority=task.priority)
                return local_handler(task)

            event(
                "cluster_task_delegated",
                task_id=task.task_id,
                kind=task.kind,
                priority=task.priority,
                origin_node_id=task.origin_node_id,
                executor_node_id=node.node_id,
            )
            result = self.transport.submit(node, task)
            if (
                result.task_id != task.task_id
                or result.origin_node_id != task.origin_node_id
                or result.request_id != task.request_id
                or result.executor_node_id != node.node_id
            ):
                raise RuntimeError(
                    "Удалённый узел вернул ответ с несовпадающим task/origin/request/executor"
                )
            return result
        finally:
            with self._lock:
                remaining = self._inflight.get(node.node_id, 1) - 1
                if remaining > 0:
                    self._inflight[node.node_id] = remaining
                else:
                    self._inflight.pop(node.node_id, None)

    def status(self) -> dict[str, Any]:
        peers = self.peers()
        with self._lock:
            pending = dict(self._inflight)
        return {
            "inflight_by_node": pending,
            "local_node_id": self.local_node.node_id,
            "peer_count": len(peers),
            "discovery_enabled": self.discovery is not None,
            "remote_transport_enabled": self.transport is not None,
            "modes": [COOPERATIVE_MODE, MODEL_PARALLEL_MODE],
            "peers": [
                {
                    "node_id": node.node_id,
                    "endpoint": node.endpoint,
                    "cpu_threads": node.cpu_threads,
                    "gpu_names": list(node.gpu_names),
                    "npu_names": list(node.npu_names),
                    "models": list(node.models),
                    "memory_total_gb": round(node.memory_total_gb, 2),
                    "memory_available_gb": round(node.memory_available_gb, 2),
                    "queue_depth": node.queue_depth,
                    "load": node.load,
                    "accepting_tasks": node.accepting_tasks,
                    "last_seen": node.last_seen,
                }
                for node in peers
            ],
        }


__all__ = [
    "COOPERATIVE_MODE",
    "MODEL_PARALLEL_MODE",
    "ClusterNode",
    "ClusterTask",
    "ClusterResult",
    "PeerDiscovery",
    "ClusterTransport",
    "RabbitMQTopology",
    "ClusterOrchestrator",
    "cluster_public_summary",
]
