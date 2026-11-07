from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from typing import Any
from urllib import request

from .cluster_orchestrator import ClusterNode, ClusterOrchestrator, ClusterResult, ClusterTask
from .cluster_controls import autodiscovery_enabled
from .version import VERSION
from .diagnostics import event, problem
from .inference_scheduler import get_inference_scheduler
from .llm import ChatMessage


_DISCOVERY_PORT = 38765
_DISCOVERY_MAGIC = "leshy-cluster-v1"
_MAX_DISCOVERY_PACKET = 24_000
_ALLOWED_REMOTE_KINDS = {"agent.prompt", "knowledge.search", "graph.search", "cluster.ping"}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hmac(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, "sha256").hexdigest()


def _memory_info() -> tuple[float, float]:
    try:
        import psutil  # type: ignore

        value = psutil.virtual_memory()
        return float(value.total) / (1024 ** 3), float(value.available) / (1024 ** 3)
    except Exception:
        return 0.0, 0.0


def _load_ratio() -> float:
    cpu_count = max(1, int(os.cpu_count() or 1))
    try:
        return max(0.0, float(os.getloadavg()[0]) / cpu_count)
    except Exception:
        return 0.0


def _local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        sock.close()


class ClusterIdentity:
    """Постоянный node_id и общий секрет доверенного кластера."""

    def __init__(self, brain_dir: Path | str):
        self.root = Path(brain_dir) / "cluster"
        self.root.mkdir(parents=True, exist_ok=True)
        self.node_path = self.root / "node-id.txt"
        self.secret_path = self.root / "cluster-secret.txt"
        self.node_id = self._load_or_create(self.node_path, lambda: secrets.token_hex(12))
        env_secret = os.getenv("LESHY_CLUSTER_SECRET", "").strip()
        if env_secret:
            self.secret = env_secret.encode("utf-8")
        else:
            value = self._load_or_create(self.secret_path, lambda: secrets.token_urlsafe(32))
            self.secret = value.encode("utf-8")
        self.cluster_id = sha256(self.secret).hexdigest()[:20]

    @staticmethod
    def _load_or_create(path: Path, factory) -> str:
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except Exception:
            pass
        value = str(factory()).strip()
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value + "\n", encoding="utf-8")
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except Exception:
            pass
        return value


class LanPeerDiscovery:
    """Автоматическое обнаружение доверенных узлов в одной локальной сети."""

    def __init__(self, identity: ClusterIdentity, *, port: int = _DISCOVERY_PORT):
        self.identity = identity
        self.port = int(port)
        self._peers: dict[str, ClusterNode] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_node: ClusterNode | None = None
        self._stats = {
            "accepted": 0, "other_cluster": 0, "invalid_signature": 0,
            "same_node_id": 0, "invalid_packet": 0, "announcements_failed": 0,
        }
        self._last_packet_at = 0.0
        self._last_accepted_at = 0.0
        self._last_error = ""

    def _record(self, name: str, *, detail: str = "") -> None:
        with self._lock:
            self._stats[name] = self._stats.get(name, 0) + 1
            self._last_packet_at = time.time()
            if name == "accepted":
                self._last_accepted_at = self._last_packet_at
            if detail:
                self._last_error = detail[:200]

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "port_udp": self.port,
                "listener_running": bool(self._thread and self._thread.is_alive()),
                "counts": dict(self._stats),
                "last_packet_at": self._last_packet_at,
                "last_accepted_at": self._last_accepted_at,
                "last_error": self._last_error,
            }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="leshy-cluster-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        with self._lock:
            self._peers.clear()

    def announce(self, node: ClusterNode) -> None:
        self._last_node = node
        payload = {
            "magic": _DISCOVERY_MAGIC,
            "cluster_id": self.identity.cluster_id,
            "node": asdict(node),
        }
        signature = _hmac(self.identity.secret, _canonical(payload))
        packet = _canonical({**payload, "signature": signature})
        if len(packet) > _MAX_DISCOVERY_PACKET:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, ("255.255.255.255", self.port))
        except Exception as exc:
            self._record("announcements_failed", detail=type(exc).__name__)
            problem("cluster_discovery_announce_failed", str(exc))
        finally:
            sock.close()

    def peers(self) -> list[ClusterNode]:
        now = time.time()
        with self._lock:
            stale = [key for key, value in self._peers.items() if now - value.last_seen > 60.0]
            for key in stale:
                self._peers.pop(key, None)
            return list(self._peers.values())

    def _accept_packet(self, raw: bytes) -> None:
        try:
            data = json.loads(raw.decode("utf-8"))
            signature = str(data.pop("signature", ""))
            if data.get("magic") != _DISCOVERY_MAGIC:
                self._record("invalid_packet")
                return
            if data.get("cluster_id") != self.identity.cluster_id:
                self._record("other_cluster")
                return
            expected = _hmac(self.identity.secret, _canonical(data))
            if not hmac.compare_digest(signature, expected):
                self._record("invalid_signature")
                return
            node_data = data.get("node") or {}
            node_id = str(node_data.get("node_id") or "")
            if not node_id:
                self._record("invalid_packet")
                return
            if node_id == self.identity.node_id:
                self._record("same_node_id")
                return
            peer = ClusterNode(
                node_id=node_id,
                endpoint=str(node_data.get("endpoint") or ""),
                cpu_threads=max(1, int(node_data.get("cpu_threads") or 1)),
                gpu_names=tuple(str(x) for x in node_data.get("gpu_names") or ()),
                npu_names=tuple(str(x) for x in node_data.get("npu_names") or ()),
                models=tuple(str(x) for x in node_data.get("models") or ()),
                memory_total_gb=float(node_data.get("memory_total_gb") or 0.0),
                memory_available_gb=float(node_data.get("memory_available_gb") or 0.0),
                queue_depth=max(0, int(node_data.get("queue_depth") or 0)),
                load=max(0.0, float(node_data.get("load") or 0.0)),
                accepting_tasks=bool(node_data.get("accepting_tasks", True)),
                last_seen=time.time(),
            )
            if not peer.endpoint.startswith(("http://", "https://")):
                return
            with self._lock:
                self._peers[peer.node_id] = peer
            self._record("accepted")
        except Exception:
            self._record("invalid_packet")
            return

    def _loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", self.port))
            sock.settimeout(1.0)
            next_announce = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                if self._last_node is not None and now >= next_announce:
                    self.announce(self._last_node)
                    next_announce = now + 5.0
                try:
                    raw, _address = sock.recvfrom(_MAX_DISCOVERY_PACKET)
                    self._accept_packet(raw)
                except socket.timeout:
                    continue
                except OSError:
                    if not self._stop.is_set():
                        time.sleep(0.2)
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            problem("cluster_discovery_listener_failed", str(exc))
        finally:
            sock.close()


class HttpClusterTransport:
    """Подписанный HTTP transport между доверенными узлами Лешего."""

    def __init__(self, identity: ClusterIdentity, *, timeout: float = 180.0):
        self.identity = identity
        self.timeout = max(5.0, float(timeout))

    def submit(self, node: ClusterNode, task: ClusterTask) -> ClusterResult:
        payload = {
            "task": {
                "task_id": task.task_id,
                "origin_node_id": task.origin_node_id,
                "reply_to": task.reply_to,
                "owner_id": task.owner_id,
                "session_id": task.session_id,
                "request_id": task.request_id,
                "priority": task.priority,
                "kind": task.kind,
                "payload": task.payload,
                "mode": task.mode,
                "model": task.model,
                "requirements": list(task.requirements),
                "created_at": task.created_at,
                "ttl_seconds": task.ttl_seconds,
            }
        }
        raw = _canonical(payload)
        timestamp = str(int(time.time()))
        signature = _hmac(self.identity.secret, timestamp.encode("ascii") + b"\n" + raw)
        req = request.Request(
            node.endpoint.rstrip("/") + "/v1/cluster/task",
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Leshy-Cluster-Id": self.identity.cluster_id,
                "X-Leshy-Cluster-Node": self.identity.node_id,
                "X-Leshy-Cluster-Time": timestamp,
                "X-Leshy-Cluster-Signature": signature,
            },
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return ClusterResult(
            task_id=str(data.get("task_id") or task.task_id),
            origin_node_id=str(data.get("origin_node_id") or task.origin_node_id),
            executor_node_id=str(data.get("executor_node_id") or node.node_id),
            request_id=str(data.get("request_id") or task.request_id),
            ok=bool(data.get("ok")),
            payload=dict(data.get("payload") or {}),
            error=str(data.get("error") or ""),
            finished_at=float(data.get("finished_at") or time.time()),
        )


class DistributedClusterRuntime:
    """Объединяет ресурсы нескольких компьютеров в вычислительный пул Лешего."""

    def __init__(self, app):
        self.app = app
        self.identity = ClusterIdentity(app.settings.brain_dir)
        self.discovery = LanPeerDiscovery(self.identity)
        self.transport = HttpClusterTransport(self.identity)
        self.local_node = self._build_local_node()
        self.orchestrator = ClusterOrchestrator(
            self.local_node,
            discovery=self.discovery,
            transport=self.transport,
            peer_ttl_seconds=60.0,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._configuration_lock = threading.RLock()
        self._autodiscovery_configured = autodiscovery_enabled(app.storage)
        self._console_nodes: tuple[str, ...] | None = None
        self._console_notice = ""
        self.orchestrator.discovery = self.discovery if self._autodiscovery_configured else None

    def _models(self) -> tuple[str, ...]:
        values: list[str] = []
        model_path = getattr(self.app, "model_path", None)
        if model_path is not None:
            values.append(str(getattr(model_path, "name", model_path)))
        model = str(getattr(getattr(self.app, "settings", None), "ollama_model", "") or "")
        if model:
            values.append(model)
        return tuple(dict.fromkeys(value for value in values if value))

    def _build_local_node(self) -> ClusterNode:
        hardware = getattr(self.app, "hardware", None)
        gpu_names = tuple(str(x) for x in (getattr(hardware, "gpu_names", ()) or ()))
        npu_names = tuple(str(x) for x in (getattr(hardware, "npu_names", ()) or ()))
        memory_total, memory_available = _memory_info()
        endpoint = os.getenv("LESHY_CLUSTER_ADVERTISE", "").strip()
        if not endpoint:
            port = int(os.getenv("LESHY_PORT", "8765"))
            endpoint = f"http://{_local_ip()}:{port}"
        scheduler = get_inference_scheduler(self.app)
        scheduler_state = scheduler.status()
        # An active generation occupies the only local inference slot, even if nobody is queued.
        queue_depth = int(scheduler_state.get("queue_depth") or 0) + int(bool(scheduler_state.get("active")))
        return ClusterNode(
            node_id=self.identity.node_id,
            endpoint=endpoint.rstrip("/"),
            cpu_threads=max(1, int(os.cpu_count() or 1)),
            gpu_names=gpu_names,
            npu_names=npu_names,
            models=self._models(),
            memory_total_gb=memory_total,
            memory_available_gb=memory_available,
            queue_depth=queue_depth,
            load=_load_ratio(),
            accepting_tasks=True,
            last_seen=time.time(),
        )

    def refresh_local_node(self) -> None:
        refreshed = self._build_local_node()
        current = self.local_node
        current.endpoint = refreshed.endpoint
        current.cpu_threads = refreshed.cpu_threads
        current.gpu_names = refreshed.gpu_names
        current.npu_names = refreshed.npu_names
        current.models = refreshed.models
        current.memory_total_gb = refreshed.memory_total_gb
        current.memory_available_gb = refreshed.memory_available_gb
        current.queue_depth = refreshed.queue_depth
        current.load = refreshed.load
        current.accepting_tasks = refreshed.accepting_tasks
        current.last_seen = time.time()

    def set_autodiscovery(self, enabled: bool) -> None:
        """Пуск/остановка UDP discovery без остановки LLM и без перезапуска Web."""
        with self._configuration_lock:
            self._autodiscovery_configured = bool(enabled)
            running = bool(self._thread and self._thread.is_alive())
            if enabled:
                self.orchestrator.discovery = self.discovery
                if running:
                    self.discovery.start()
            else:
                self.orchestrator.discovery = None
                self.discovery.stop()
                self.orchestrator.forget_remote_peers()
            self._console_nodes = None
        print(f"[Леший {VERSION}] Автообнаружение LAN: {'ВКЛ' if enabled else 'ВЫКЛ'}", flush=True)
        event("cluster_autodiscovery_changed", enabled=bool(enabled))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._configuration_lock:
            if self._autodiscovery_configured:
                self.orchestrator.discovery = self.discovery
                self.discovery.start()
            else:
                self.orchestrator.discovery = None
                self.orchestrator.forget_remote_peers()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="leshy-cluster-runtime", daemon=True)
        self._thread.start()
        print(
            f"[Леший {VERSION}] Кластер: ВКЛ | узел {self.identity.node_id[:12]} "
            f"| cluster_id {self.identity.cluster_id[:12]} | UDP {_DISCOVERY_PORT}: "
            f"{'автопоиск' if self._autodiscovery_configured else 'выключен'}",
            flush=True,
        )
        event(
            "distributed_cluster_started",
            node_id=self.identity.node_id,
            cluster_id=self.identity.cluster_id,
            endpoint=self.local_node.endpoint,
        )

    def stop(self) -> None:
        self._stop.set()
        self.discovery.stop()
        self.orchestrator.forget_remote_peers()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._console_nodes = None
        print(f"[Леший {VERSION}] Кластер: ВЫКЛ", flush=True)

    def _loop(self) -> None:
        while not self._stop.wait(5.0):
            try:
                self.refresh_local_node()
                self.orchestrator.announce_and_refresh()
                self._console_diagnostics()
            except Exception as exc:
                problem("distributed_cluster_refresh_failed", str(exc))

    def refresh(self) -> list[ClusterNode]:
        self.refresh_local_node()
        return self.orchestrator.announce_and_refresh()

    def _console_diagnostics(self) -> None:
        """Печатаем изменения состояния, не засоряя консоль каждые 5 секунд."""
        peers = self.orchestrator.peers()
        nodes = tuple(sorted(peer.node_id for peer in peers))
        if nodes != self._console_nodes:
            self._console_nodes = nodes
            print(
                f"[Леший {VERSION}] Кластер: {len(nodes)} узл. | "
                f"автообнаружение {'ВКЛ' if self._autodiscovery_configured else 'ВЫКЛ'} "
                f"| задач в работе {sum(self.orchestrator.status()['inflight_by_node'].values())}",
                flush=True,
            )
        counters = self.discovery.diagnostics()["counts"]
        issue = (
            "обнаружены объявления ДРУГОГО кластера — проверьте cluster-secret"
            if counters.get("other_cluster") and len(nodes) < 2 else
            "чужой узел использует тот же node_id"
            if counters.get("same_node_id", 0) > 10 and len(nodes) < 2 else ""
        )
        if issue and issue != self._console_notice:
            self._console_notice = issue
            print(f"[Леший {VERSION}] Диагностика LAN: {issue}", flush=True)

    def diagnostics(self, *, cluster_enabled: bool = True) -> dict[str, Any]:
        self.refresh_local_node()
        state = self.status()
        discovery = self.discovery.diagnostics()
        remote = max(0, int(state["peer_count"]) - 1) if cluster_enabled else 0
        hints: list[str] = []
        if not cluster_enabled:
            hints.append("Кластер выключен: включите переключатель в настройках.")
        elif not self._autodiscovery_configured:
            hints.append("Автообнаружение выключено. Другие компьютеры не ищутся.")
        elif not discovery["listener_running"]:
            hints.append("UDP-приёмник не работает: проверьте порт 38765 и повторно включите обнаружение.")
        if cluster_enabled and self._autodiscovery_configured and remote == 0:
            counts = discovery["counts"]
            if counts.get("other_cluster"):
                hints.append("В сети есть Леший с другим cluster_id: проверьте общий cluster-secret.txt.")
            if counts.get("invalid_signature"):
                hints.append("Получены объявления с неверной подписью: проверьте общий секрет.")
            if counts.get("same_node_id", 0) > 10:
                hints.append("Объявления совпадают с локальным node_id: проверьте уникальность node-id.txt.")
            hints.append("Другие доверенные узлы не найдены: проверьте UDP 38765, локальную сеть и Windows Firewall.")
        if discovery["last_error"]:
            hints.append("Ошибка UDP: " + discovery["last_error"])
        return {
            "version": VERSION,
            "enabled": bool(cluster_enabled),
            "autodiscovery_enabled": self._autodiscovery_configured,
            "cluster_id": self.identity.cluster_id,
            "node_id": self.identity.node_id,
            "endpoint": self.local_node.endpoint,
            "peer_count": state["peer_count"] if cluster_enabled else 0,
            "remote_peers": state["peers"] if cluster_enabled else [],
            "inflight_by_node": state["inflight_by_node"] if cluster_enabled else {},
            "udp": discovery,
            "tcp_port": int(os.getenv("LESHY_PORT", "8765")),
            "hints": hints,
        }

    def _execute_local(self, task: ClusterTask) -> ClusterResult:
        try:
            if task.kind == "cluster.ping":
                payload = {"status": "ok", "node_id": self.identity.node_id}
            elif task.kind == "knowledge.search":
                query = str(task.payload.get("query") or "")
                payload = {"items": self.app.knowledge.search(query, trusted_only=True, limit=8)}
            elif task.kind == "graph.search":
                graph = getattr(self.app, "knowledge_graph", None)
                if graph is None:
                    raise RuntimeError("Knowledge Graph на этом узле не инициализирован")
                query = str(task.payload.get("query") or "")
                payload = {"items": [item.public() for item in graph.search(task.owner_id, query, limit=8)]}
            elif task.kind == "agent.prompt":
                system = str(task.payload.get("system") or "Ты подагент Лешего. Не выдумывай факты.")
                prompt = str(task.payload.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("Пустая задача подагента")
                scheduler = get_inference_scheduler(self.app)
                with scheduler.slot(source=f"cluster:{task.origin_node_id}", priority=int(task.priority)):
                    answer = self.app.llm.chat([ChatMessage("system", system), ChatMessage("user", prompt)])
                payload = {"answer": str(answer or "").strip()}
            else:
                raise PermissionError(f"Кластерная задача запрещена: {task.kind}")
            return ClusterResult(
                task_id=task.task_id,
                origin_node_id=task.origin_node_id,
                executor_node_id=self.identity.node_id,
                request_id=task.request_id,
                ok=True,
                payload=payload,
            )
        except Exception as exc:
            problem("cluster_task_local_failed", str(exc), kind=task.kind, task_id=task.task_id)
            return ClusterResult(
                task_id=task.task_id,
                origin_node_id=task.origin_node_id,
                executor_node_id=self.identity.node_id,
                request_id=task.request_id,
                ok=False,
                error=str(exc),
            )

    def submit_agent_prompt(
        self,
        *,
        owner_id: str,
        session_id: str,
        request_id: str,
        role: str,
        system: str,
        prompt: str,
        priority: int = 60,
    ) -> ClusterResult:
        self.refresh()
        task = ClusterTask.create(
            origin_node_id=self.identity.node_id,
            reply_to=self.local_node.endpoint,
            owner_id=owner_id,
            session_id=session_id,
            request_id=request_id,
            priority=priority,
            kind="agent.prompt",
            payload={"role": role, "system": system, "prompt": prompt},
            ttl_seconds=180.0,
        )
        return self.orchestrator.execute_or_delegate(task, self._execute_local)

    def verify_request(self, body: dict[str, Any], headers) -> None:
        cluster_id = str(headers.get("X-Leshy-Cluster-Id") or "")
        timestamp = str(headers.get("X-Leshy-Cluster-Time") or "")
        signature = str(headers.get("X-Leshy-Cluster-Signature") or "")
        if cluster_id != self.identity.cluster_id:
            raise PermissionError("Узел принадлежит другому кластеру")
        try:
            ts = int(timestamp)
        except ValueError as exc:
            raise PermissionError("Некорректная метка времени кластера") from exc
        if abs(int(time.time()) - ts) > 90:
            raise PermissionError("Подпись кластерного запроса устарела")
        expected = _hmac(self.identity.secret, timestamp.encode("ascii") + b"\n" + _canonical(body))
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("Неверная подпись кластерного запроса")

    def handle_remote_task(self, body: dict[str, Any], headers) -> dict[str, Any]:
        self.verify_request(body, headers)
        raw = dict(body.get("task") or {})
        kind = str(raw.get("kind") or "")
        if kind not in _ALLOWED_REMOTE_KINDS:
            raise PermissionError(f"Удалённый тип задачи запрещён: {kind}")
        task = ClusterTask(
            task_id=str(raw.get("task_id") or ""),
            origin_node_id=str(raw.get("origin_node_id") or ""),
            reply_to=str(raw.get("reply_to") or ""),
            owner_id=str(raw.get("owner_id") or "cluster")[:200],
            session_id=str(raw.get("session_id") or "")[:200],
            request_id=str(raw.get("request_id") or "")[:200],
            priority=int(raw.get("priority") or 60),
            kind=kind,
            payload=dict(raw.get("payload") or {}),
            mode=str(raw.get("mode") or "cooperative"),
            model=str(raw.get("model") or ""),
            requirements=tuple(str(x) for x in raw.get("requirements") or ()),
            created_at=float(raw.get("created_at") or time.time()),
            ttl_seconds=max(1.0, float(raw.get("ttl_seconds") or 120.0)),
        )
        if not task.task_id or not task.origin_node_id or task.expired():
            raise ValueError("Некорректная или просроченная кластерная задача")
        result = self._execute_local(task)
        return asdict(result)

    def status(self) -> dict[str, Any]:
        self.refresh_local_node()
        value = self.orchestrator.status()
        value.update({
            "enabled": True,
            "cluster_id": self.identity.cluster_id,
            "node_id": self.identity.node_id,
            "advertise_endpoint": self.local_node.endpoint,
            "secret_path": str(self.identity.secret_path),
            "monetization": False,
            "proof_of_work": False,
            "purpose": "полезные вычисления Лешего",
        })
        return value


__all__ = [
    "ClusterIdentity",
    "LanPeerDiscovery",
    "HttpClusterTransport",
    "DistributedClusterRuntime",
]
