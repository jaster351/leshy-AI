from __future__ import annotations

"""Автообновление Windows-узлов по объявлениям доверенного LAN-кластера.

Одноранговый узел сообщает только версию; исполняемый код берётся из
канонического GitHub/main штатным AutoBuilder. Не выполняем код от peers.
"""

from hashlib import sha256
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib import parse, request

from .diagnostics import event, problem
from .distributed_cluster import _canonical, _hmac, _local_ip
from .version import VERSION

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_INTERVAL = 60.0
_RETRY_INTERVAL = 1800.0
_MAX_RESPONSE = 16384


def _parts(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(str(value or "").strip())
    return tuple(map(int, match.groups())) if match else None


def _enabled() -> bool:
    return os.getenv("LESHY_CLUSTER_AUTO_UPDATE", "").strip().lower() in {"1", "true", "yes", "on"}


class ClusterAutoUpdate:
    """Не блокирует HTTP или LLM. Требует явного включения на каждом ПК."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.enabled = _enabled()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_peer_version = ""
        self._last_peer = ""
        self._last_attempt = ""
        self._last_error = ""
        self._last_attempt_at = 0.0
        self._launched = False

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "current_version": VERSION,
                "peer_version": self._last_peer_version,
                "peer_node": self._last_peer,
                "last_attempt": self._last_attempt,
                "last_error": self._last_error,
                "update_launched": self._launched,
                "mode": "trusted-lan-announcement+canonical-github-main",
            }

    @staticmethod
    def _is_lan_host(hostname: str) -> bool:
        try:
            address = ipaddress.ip_address(hostname)
            local = ipaddress.ip_address(_local_ip())
            if address.version != 4 or local.version != 4:
                return False
            if not address.is_private or address.is_loopback or address.is_link_local:
                return False
            configured = os.getenv("LESHY_CLUSTER_UPDATE_SUBNETS", "").strip()
            if configured:
                networks = [ipaddress.ip_network(item.strip(), strict=False)
                            for item in configured.split(",") if item.strip()]
                return any(address in net for net in networks)
            return address in ipaddress.ip_network(f"{local}/24", strict=False)
        except ValueError:
            return False

    def _is_known_peer(self, peer_id: str, remote_ip: str) -> bool:
        for peer in self.runtime.discovery.peers():
            host = parse.urlsplit(peer.endpoint).hostname or ""
            if peer.node_id == peer_id and host == remote_ip and self._is_lan_host(host):
                return True
        return False

    def describe(self, body: dict, headers, remote_ip: str) -> dict:
        """Отвечает только на HMAC-подписанный запрос подтверждённого LAN-пира."""
        self.runtime.verify_request(body, headers)
        origin = str(headers.get("X-Leshy-Cluster-Node") or "")
        if not self._is_known_peer(origin, remote_ip):
            raise PermissionError("Неизвестный LAN-узел")
        if body.get("operation") != "describe":
            raise ValueError("Недопустимая операция обновления")
        nonce = str(body.get("nonce") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise ValueError("Некорректный nonce")
        payload = {
            "node_id": self.runtime.identity.node_id,
            "version": VERSION,
            "nonce": nonce,
            "protocol": "leshy-lan-update-v1",
        }
        return {**payload, "signature": _hmac(self.runtime.identity.secret, _canonical(payload))}

    def _describe_peer(self, node) -> str:
        host = parse.urlsplit(node.endpoint).hostname or ""
        if not self._is_lan_host(host):
            return ""
        nonce = secrets.token_hex(16)
        body = {"operation": "describe", "nonce": nonce}
        raw = _canonical(body)
        stamp = str(int(time.time()))
        endpoint = node.endpoint.rstrip("/") + "/v1/cluster/update-info"
        req = request.Request(
            endpoint, data=raw, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Leshy-Cluster-Id": self.runtime.identity.cluster_id,
                "X-Leshy-Cluster-Node": self.runtime.identity.node_id,
                "X-Leshy-Cluster-Time": stamp,
                "X-Leshy-Cluster-Signature": _hmac(
                    self.runtime.identity.secret, stamp.encode("ascii") + b"\n" + raw
                ),
            },
        )
        # Отменяем системный proxy для приватных сетевых адресов.
        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=5) as response:
            payload_raw = response.read(_MAX_RESPONSE + 1)
        if len(payload_raw) > _MAX_RESPONSE:
            raise ValueError("Ответ LAN-узла слишком велик")
        data = json.loads(payload_raw)
        signature = str(data.pop("signature", ""))
        if not hmac.compare_digest(signature, _hmac(self.runtime.identity.secret, _canonical(data))):
            raise PermissionError("Неверная подпись объявления версии")
        if data.get("nonce") != nonce or data.get("node_id") != node.node_id:
            raise PermissionError("Ответ не соответствует ожидаемому узлу")
        if data.get("protocol") != "leshy-lan-update-v1":
            raise ValueError("Несовместимый протокол обновления")
        return str(data.get("version") or "")

    @staticmethod
    def _auto_builder() -> tuple[Path, Path] | None:
        if os.name != "nt" or not getattr(sys, "frozen", False):
            return None
        install = Path(sys.executable).resolve().parent
        # Запущен именно dist\Leshy\Leshy.exe в рабочем Git-дереве.
        if install.name.lower() != "leshy" or install.parent.name.lower() != "dist":
            return None
        root = install.parent.parent
        script = root / "windows" / "autobuilder-core.ps1"
        if not script.is_file() or not (root / ".git").exists():
            return None
        return root, script

    def _launch(self, target_version: str, peer_id: str) -> None:
        target = self._auto_builder()
        if not target:
            raise RuntimeError("Автообновление требует Windows-сборку dist\\Leshy с AutoBuilder и Git")
        root, script = target
        logfile = root / ".autobuild" / "logs" / "cluster-auto-update.log"
        logfile.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-Mode", "full-update", "-RepositoryPath", str(root),
            "-Remote", "origin", "-Branch", "main",
            "-AutoFromCluster", "-MinimumVersion", target_version,
        ]
        # Отдельный процесс переживает завершение самого Leshy.exe.
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with logfile.open("a", encoding="utf-8") as output:
            subprocess.Popen(
                command, cwd=str(root), stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.STDOUT, creationflags=flags, close_fds=True,
            )
        with self._lock:
            self._launched = True
        event("cluster_auto_update_launched", peer=peer_id, target_version=target_version)

    def _cycle(self) -> None:
        local = _parts(VERSION)
        if local is None:
            return
        candidates = []
        for node in self.runtime.discovery.peers():
            try:
                peer_version = self._describe_peer(node)
                candidate = _parts(peer_version)
                if candidate and candidate > local:
                    candidates.append((candidate, peer_version, node.node_id))
            except Exception as exc:
                # До установки одинаковой версии протокол может отсутствовать.
                event("cluster_update_peer_unavailable", peer=node.node_id, error_type=type(exc).__name__)
        if not candidates:
            return
        _, version, peer_id = max(candidates)
        with self._lock:
            self._last_peer_version = version
            self._last_peer = peer_id
            if self._launched or time.monotonic() - self._last_attempt_at < _RETRY_INTERVAL:
                return
            self._last_attempt_at = time.monotonic()
            self._last_attempt = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            self._launch(version, peer_id)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)[:250]
            problem("cluster_auto_update_launch_failed", type(exc).__name__, peer=peer_id)

    def _loop(self) -> None:
        # Первую проверку не делаем во время загрузки GGUF / восстановления чата.
        if self._stop.wait(30):
            return
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as exc:
                problem("cluster_auto_update_cycle_failed", type(exc).__name__)
            self._stop.wait(_INTERVAL)

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="leshy-cluster-auto-update", daemon=True)
        self._thread.start()
        event("cluster_auto_update_started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
