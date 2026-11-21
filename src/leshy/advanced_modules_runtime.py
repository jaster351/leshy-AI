from __future__ import annotations

import json
import re
import uuid
from typing import Any, Iterator

from .diagnostics import event, problem
from .distributed_cluster import DistributedClusterRuntime
from .intelligent_memory import IntelligentMemoryEngine, _canonical_memory
from .knowledge_graph import KnowledgeGraph
from .module_settings import module_enabled
from .multi_agent_orchestrator import MultiAgentOrchestrator
from .realtime_chat import RealtimeChatManager, StreamEvent
from .storage import utcnow


_INSTALLED = False
_ORIGINAL_CHAT = None
_ORIGINAL_STREAM = None
_ORIGINAL_MEMORY_CONTEXT = None
_ORIGINAL_STOP_BACKGROUND = None


def _ensure_components(app) -> None:
    if not isinstance(getattr(app, "intelligent_memory", None), IntelligentMemoryEngine):
        app.intelligent_memory = IntelligentMemoryEngine(app.storage)
    if not isinstance(getattr(app, "knowledge_graph", None), KnowledgeGraph):
        app.knowledge_graph = KnowledgeGraph(app.storage)
    if not isinstance(getattr(app, "distributed_cluster", None), DistributedClusterRuntime):
        app.distributed_cluster = DistributedClusterRuntime(app)
    if not isinstance(getattr(app, "multi_agent_orchestrator", None), MultiAgentOrchestrator):
        app.multi_agent_orchestrator = MultiAgentOrchestrator(app)

    if module_enabled(app.storage, "distributed-cluster"):
        try:
            app.distributed_cluster.start()
        except Exception as exc:
            problem("distributed_cluster_start_failed", str(exc))


def sync_advanced_module_state(app, module_name: str) -> None:
    if module_name != "distributed-cluster":
        return
    _ensure_components(app)
    if module_enabled(app.storage, "distributed-cluster"):
        app.distributed_cluster.start()
    else:
        app.distributed_cluster.stop()


def _save_user(app, message: str, owner_id: str, session_id: str | None) -> str:
    sid = app._ensure_session(owner_id, session_id)
    now = utcnow()
    with app.storage.connection() as conn:
        conn.execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
            (sid, "user", message, now),
        )
        count = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,)).fetchone()["c"])
        if count == 1:
            conn.execute(
                "UPDATE sessions SET title=?,title_source=?,updated_at=? WHERE id=?",
                (" ".join(message.split())[:80], "auto", now, sid),
            )
    return sid


def _advanced_kind(app, message: str) -> tuple[str, str] | None:
    value = " ".join(str(message or "").split()).strip()
    low = value.lower()
    if low in {"/cluster", "/cluster status", "/кластер", "/кластер статус"}:
        return "cluster", ""
    if low in {
        "/creator_consensus", "/creator-consensus", "/проверка_создателя",
        "проверь создателя у федерации", "проверь кто создатель у леших",
    }:
        return "creator_consensus", ""
    for prefix in ("/memory ", "/память "):
        if low.startswith(prefix):
            return "memory", value[len(prefix):].strip()
    for prefix in ("/graph ", "/граф ", "/graphrag "):
        if low.startswith(prefix):
            return "graph", value[len(prefix):].strip()
    orchestrator = getattr(app, "multi_agent_orchestrator", None)
    if orchestrator is not None:
        task = orchestrator.parse_request(value)
        if task:
            return "agents", task
    return None


def _cluster_answer(app) -> tuple[str, dict[str, Any]]:
    if not module_enabled(app.storage, "distributed-cluster"):
        return "Модуль «Распределённый кластер Лешего» выключен в настройках.", {}
    cluster = app.distributed_cluster
    try:
        cluster.refresh()
    except Exception as exc:
        problem("cluster_manual_refresh_failed", str(exc))
    status = cluster.status()
    peers = list(status.get("peers") or [])
    lines = [
        f"Кластер активен. Узлов: {len(peers)}.",
        f"Этот узел: {status.get('node_id') or status.get('local_node_id')}",
    ]
    if len(peers) <= 1:
        lines.append("Другие компьютеры пока не обнаружены. Для объединения у узлов должен быть один и тот же cluster secret и доступен порт Лешего в локальной сети.")
    else:
        total_cpu = sum(int(item.get("cpu_threads") or 0) for item in peers)
        total_ram = sum(float(item.get("memory_total_gb") or 0.0) for item in peers)
        gpu_count = sum(len(item.get("gpu_names") or []) for item in peers)
        npu_count = sum(len(item.get("npu_names") or []) for item in peers)
        lines.append(f"Суммарно: CPU-потоков {total_cpu}, RAM {total_ram:.1f} ГБ, GPU {gpu_count}, NPU {npu_count}.")
        lines.append("Подагенты и другие независимые полезные задачи могут распределяться между узлами автоматически.")
    lines.append("Майнинга, Proof-of-Work и монетизации нет.")
    return "\n".join(lines), status


def _creator_consensus_answer(app) -> tuple[str, dict[str, Any]]:
    """Показать коллективные свидетельства; не подменять голосованием истину."""
    if not module_enabled(app.storage, "distributed-cluster"):
        return "Для коллективной проверки включите модуль LAN-кластера Лешего.", {}
    cluster = app.distributed_cluster
    try:
        cluster.refresh()
    except Exception as exc:
        problem("creator_consensus_refresh_failed", type(exc).__name__)
    cache_state = cluster.creator_consensus.reconcile_informational_cache()
    report = cluster.creator_consensus.report()
    agreeing = int(report["matching_local"])
    total = int(report["total_observed_nodes"])
    peers = int(report["reported_peers"])
    lines = [
        "Коллективная сверка создателя Федерации Леших:",
        f"Ответов от других узлов: {peers}. Совпадений с локальным кодом: {agreeing} из {total} (включая этот узел).",
    ]
    if not peers:
        lines.append("Другие узлы ещё не передали свежие сведения. Общее мнение отсутствует.")
    elif report["status"] == "consistent":
        lines.append("Полученные отпечатки одинаковы; расхождений среди ответивших нет.")
    else:
        lines.append("Обнаружены расхождения! Нужна проверка узлов и их сборок.")
        if report["differing_peer_ids"]:
            lines.append("Узлы с иным отпечатком: " +
                         ", ".join(report["differing_peer_ids"][:16]))
        if report["status"] == "local_outlier":
            lines.append("В этом узле встроенный отпечаток отличается от большинства; исходный код не изменён.")
    lines.append(f"Служебная копия сведений о создателе: {cache_state}.")
    lines.append(
        "Большинство и общий секрет кластера не доказывают авторство; "
        "роли, личная память и встроенный код голосованием не изменяются."
    )
    return "\n".join(lines), report


def _handle_advanced(app, kind: str, payload: str, *, owner_id: str, session_id: str, request_id: str) -> tuple[str, dict[str, Any]]:
    if kind == "cluster":
        return _cluster_answer(app)
    if kind == "creator_consensus":
        return _creator_consensus_answer(app)
    if kind == "memory":
        if not module_enabled(app.storage, "intelligent-memory"):
            return "Модуль «Интеллектуальная память» выключен в настройках.", {}
        query = payload.strip()
        if not query:
            return "Укажите, что найти в памяти: `/memory запрос`.", {}
        rows = app.intelligent_memory.retrieve(owner_id, query, limit=10)
        if not rows:
            return "В долговременной памяти не найдено релевантных записей.", {"items": []}
        answer = "Нашёл в памяти:\n" + "\n".join(f"• {item.content}" for item in rows)
        return answer, {"items": [item.public() for item in rows]}
    if kind == "graph":
        if not module_enabled(app.storage, "knowledge-graph"):
            return "Модуль «Knowledge Graph / GraphRAG» выключен в настройках.", {}
        query = payload.strip()
        if not query:
            return "Укажите запрос: `/graph запрос`.", {}
        rows = app.knowledge_graph.search(owner_id, query, limit=10)
        if not rows:
            return "В графе не найдено связанных фактов.", {"items": []}
        answer = "Связанные факты GraphRAG:\n" + "\n".join(f"• {item.text}" for item in rows)
        return answer, {"items": [item.public() for item in rows]}
    if kind == "agents":
        if not module_enabled(app.storage, "agent-orchestrator"):
            return "Модуль «Оркестратор подагентов» выключен в настройках.", {}
        result = app.multi_agent_orchestrator.run(
            payload,
            owner_id=owner_id,
            session_id=session_id,
            request_id=request_id,
        )
        return str(result.get("answer") or ""), result
    raise ValueError(f"Неизвестный advanced route: {kind}")



def _memory_items(text: str) -> list[str]:
    """Целые элементы списка, включая строки продолжения, без заголовков."""
    items: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)(.*)$", line)
        if match or not line.strip():
            if current:
                items.append("\n".join(current).strip())
                current = []
            if match:
                current = [match.group(1)]
        elif current:
            current.append(line.strip())
    if current:
        items.append("\n".join(current).strip())
    return items


def _compose_memory_context(app, owner_id: str, original: str, *,
                            query: str | None = None, capture_text: str | None = None) -> str:
    """Всегда сохранять проверенные правила базового профиля поверх Mem0 и GraphRAG."""
    blocks: list[str] = []
    if original:
        blocks.append(original)
    # Нет текущего запроса — сохраняем профиль, но не угадываем тему по последнему
    # сообщению владельца: оно может принадлежать параллельному другому чату.
    if not isinstance(query, str) or not query.strip():
        return "\n\n".join(blocks)
    query = query.strip()
    if module_enabled(app.storage, "intelligent-memory"):
        memory = app.intelligent_memory.context(
            owner_id, query, limit=8, capture_text=capture_text if isinstance(capture_text, str) else "")
        if memory:
            # В исходном контексте уже есть постоянные правила и обычная память.
            # Снова найденные записи не должны расходовать токены повторно.
            extra = []
            seen = {_canonical_memory(item) for item in _memory_items(original)}
            for content in _memory_items(memory):
                key = _canonical_memory(content)
                if key and key not in seen:
                    extra.append("- " + content)
                    seen.add(key)
            if extra:
                blocks.append("Дополнительная интеллектуальная память:\n" + "\n".join(extra))
    if module_enabled(app.storage, "knowledge-graph") and query:
        graph = app.knowledge_graph.context(owner_id, query, limit=7)
        if graph:
            blocks.append("Связанные знания GraphRAG:\n" + graph)
    return "\n\n".join(blocks)


def install_advanced_modules(app_class, app=None) -> None:
    global _INSTALLED, _ORIGINAL_CHAT, _ORIGINAL_STREAM, _ORIGINAL_MEMORY_CONTEXT, _ORIGINAL_STOP_BACKGROUND
    if app is not None:
        _ensure_components(app)
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_CHAT = app_class.chat
    _ORIGINAL_STREAM = RealtimeChatManager.stream
    _ORIGINAL_MEMORY_CONTEXT = app_class._memory_context
    _ORIGINAL_STOP_BACKGROUND = app_class.stop_background

    def memory_context(self, owner_id: str, *, query: str | None = None,
                       capture_text: str | None = None) -> str:
        _ensure_components(self)
        original = _ORIGINAL_MEMORY_CONTEXT(self, owner_id, query=query, capture_text=capture_text)
        return _compose_memory_context(self, owner_id, original, query=query, capture_text=capture_text)

    def chat(self, message: str, *, owner_id: str = "web:local", session_id: str | None = None, **kwargs):
        _ensure_components(self)
        route = _advanced_kind(self, message)
        if route is None:
            return _ORIGINAL_CHAT(self, message, owner_id=owner_id, session_id=session_id, **kwargs)
        sid = _save_user(self, str(message), owner_id, session_id)
        rid = str(kwargs.get("request_id") or uuid.uuid4())
        try:
            answer, metadata = _handle_advanced(self, route[0], route[1], owner_id=owner_id, session_id=sid, request_id=rid)
        except Exception as exc:
            problem("advanced_module_chat_failed", str(exc), kind=route[0], session_id=sid)
            answer, metadata = "Не удалось выполнить запрос модуля: " + str(exc), {"error": str(exc)}
        return {
            "session_id": sid,
            "answer": answer,
            "created_at": self._save_assistant(sid, answer),
            "sources": [],
            "advanced_module": route[0],
            "advanced_metadata": metadata,
        }

    def stream(
        self: RealtimeChatManager,
        message: str,
        *,
        owner_id: str = "web:local",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        _ensure_components(self.app)
        route = _advanced_kind(self.app, message)
        if route is None:
            yield from _ORIGINAL_STREAM(
                self,
                message,
                owner_id=owner_id,
                session_id=session_id,
                request_id=request_id,
            )
            return

        rid, cancel = self._register(request_id)
        sid = ""
        try:
            sid, _rows = self._save_user_and_history(str(message), owner_id, session_id)
            yield StreamEvent("start", {"request_id": rid, "session_id": sid, "advanced_module": route[0]})
            activity_text = {
                "agents": "Запускаю команду подагентов…",
                "cluster": "Проверяю узлы кластера…",
                "creator_consensus": "Сверяю сведения о создателе у узлов Федерации…",
                "memory": "Ищу в долговременной памяти…",
                "graph": "Ищу связи в GraphRAG…",
            }.get(route[0], "Выполняю модуль…")
            yield StreamEvent("activity", {"kind": route[0], "phase": "working", "text": activity_text})
            answer, metadata = _handle_advanced(
                self.app,
                route[0],
                route[1],
                owner_id=owner_id,
                session_id=sid,
                request_id=rid,
            )
            if cancel.is_set():
                yield StreamEvent("done", {
                    "request_id": rid,
                    "session_id": sid,
                    "answer": "",
                    "sources": [],
                    "stopped": True,
                    "advanced_module": route[0],
                })
                return
            for chunk in self._chunk_fallback(answer, 180):
                if cancel.is_set():
                    break
                yield StreamEvent("delta", {"text": chunk})
            if cancel.is_set():
                yield StreamEvent("done", {
                    "request_id": rid,
                    "session_id": sid,
                    "answer": "",
                    "sources": [],
                    "stopped": True,
                    "advanced_module": route[0],
                })
                return
            answered_at = self.app._save_assistant(sid, answer)
            yield StreamEvent("done", {
                "request_id": rid,
                "session_id": sid,
                "answer": answer,
                "created_at": answered_at,
                "sources": [],
                "stopped": False,
                "advanced_module": route[0],
                "advanced_metadata": metadata,
            })
        except Exception as exc:
            problem("advanced_module_stream_failed", str(exc), kind=route[0], session_id=sid)
            yield StreamEvent("error", {"request_id": rid, "session_id": sid, "error": str(exc), "advanced_module": route[0]})
        finally:
            self._unregister(rid)

    def stop_background(self) -> None:
        cluster = getattr(self, "distributed_cluster", None)
        if cluster is not None:
            try:
                cluster.stop()
            except Exception:
                pass
        return _ORIGINAL_STOP_BACKGROUND(self)

    app_class._memory_context = memory_context
    app_class.chat = chat
    RealtimeChatManager.stream = stream
    app_class.stop_background = stop_background
    event("advanced_modules_runtime_installed")


__all__ = ["install_advanced_modules", "sync_advanced_module_state"]
