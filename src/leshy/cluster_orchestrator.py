from __future__ import annotations

from dataclasses import dataclass, field
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
        if task.model and self.models and task.model not in self.models:
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
    """Tracker/DHT/mDNS могут реализовать один и тот же discovery-контракт."""

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


class ClusterOrchestrator:
    """Распределяет подзадачи между узлами и возвращает результат инициатору.

    В cooperative-режиме только origin/leader формирует итоговый пользовательский
    текст. Остальные узлы выполняют поиск, RAG, инструменты, анализ, ASR/TTS или
    другие независимые подзадачи и возвращают структурированные результаты по
    task_id. Это предотвращает потерю/перестановку токенов.

    model_parallel зарезервирован для настоящего distributed inference одной
    совместимой модели/runtime. Два независимых генератора не склеиваются по
    половине ответа.
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

    def register_peer(self, peer: ClusterNode) -> None:
        with self._lock:
            self._peers[peer.node_id] = peer

    def peers(self) -> list[ClusterNode]:
        with self._lock:
            return list(self._peers.values())

    @staticmethod
    def _score(node: ClusterNode, task: ClusterTask) -> tuple[float, str]:
        accelerator_bonus = 0.0
        required = set(task.requirements)
        if "gpu" in required and node.gpu_names:
            accelerator_bonus -= 25.0
        if "npu" in required and node.npu_names:
            accelerator_bonus -= 20.0
        score = node.queue_depth * 20.0 + max(0.0, node.load) * 100.0 + accelerator_bonus
        return score, node.node_id

    def choose_executor(self, task: ClusterTask) -> ClusterNode:
        candidates = [node for node in self.peers() if node.supports(task)]
        if not candidates:
            return self.local_node
        return min(candidates, key=lambda node: self._score(node, task))

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

        node = self.choose_executor(task)
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
        if result.task_id != task.task_id or result.origin_node_id != task.origin_node_id:
            raise RuntimeError("Удалённый узел вернул результат не для исходной задачи")
        return result

    def status(self) -> dict[str, Any]:
        peers = self.peers()
        return {
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
]
