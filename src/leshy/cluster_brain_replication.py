from __future__ import annotations

"""Безопасная первая стадия репликации пользовательских данных внутри LAN.

Не копирует живую SQLite-базу и не переносит узловые идентификаторы, ключи
или credentials. Обмен включается явно на каждом компьютере.
"""

import base64
from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
import json
import os
import secrets
import sqlite3
import threading
import time
from typing import Any
from urllib import parse, request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .diagnostics import event, problem
from .distributed_cluster import _canonical, _hmac, _local_ip

_BATCH = 100
_MAX_RESPONSE_BYTES = 4_000_000
_SYNC_INTERVAL_SECONDS = 30
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cluster_replication_cursors(
    peer_id TEXT PRIMARY KEY,
    session_cursor TEXT NOT NULL DEFAULT '',
    message_cursor INTEGER NOT NULL DEFAULT 0,
    memory_cursor INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cluster_replication_message_origins(
    origin_node TEXT NOT NULL,
    origin_id INTEGER NOT NULL,
    local_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    PRIMARY KEY(origin_node, origin_id)
);
CREATE INDEX IF NOT EXISTS ix_cluster_replication_local_message
    ON cluster_replication_message_origins(local_id);
"""


def _enabled() -> bool:
    return os.getenv("LESHY_CLUSTER_REPLICATION", "0").strip().lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClusterBrainReplication:
    """Низкоприоритетная, идемпотентная синхронизация SQLite между LAN-узлами."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.app = runtime.app
        self.identity = runtime.identity
        self.enabled = _enabled()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_success = ""
        self._last_error = ""
        self._imported = 0
        self._lock = threading.Lock()
        self._key = sha256(b"leshy-brain-replication-v1\x00" + self.identity.secret).digest()
        if self.enabled:
            with self.app.storage.connection() as conn:
                conn.executescript(_SCHEMA)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "mode": "chat_memory_stage1",
                "last_success": self._last_success,
                "last_error": self._last_error,
                "imported_records": self._imported,
                "interval_seconds": _SYNC_INTERVAL_SECONDS,
                "full_folder_replication": False,
            }

    def _lan_ip(self, address: str) -> bool:
        try:
            peer = ipaddress.ip_address(address)
            local = ipaddress.ip_address(_local_ip())
            if peer.version != 4 or local.version != 4 or peer.is_loopback or not peer.is_private:
                return False
            networks = os.getenv("LESHY_CLUSTER_REPLICATION_SUBNETS", "").strip()
            if networks:
                return any(peer in ipaddress.ip_network(value.strip(), strict=False)
                           for value in networks.split(",") if value.strip())
            # Консервативный режим: без явной подсети только одна LAN /24.
            return peer in ipaddress.ip_network(f"{local}/24", strict=False)
        except ValueError:
            return False

    def _known_peer(self, node_id: str) -> bool:
        return bool(node_id) and node_id != self.identity.node_id and any(
            node.node_id == node_id for node in self.runtime.orchestrator.peers()
        )

    def _seal(self, payload: dict[str, Any], request_id: str) -> dict[str, str]:
        nonce = os.urandom(12)
        data = AESGCM(self._key).encrypt(nonce, _canonical(payload), request_id.encode("ascii"))
        return {
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(data).decode("ascii"),
            "request_id": request_id,
        }

    def _unseal(self, envelope: dict[str, str], request_id: str) -> dict[str, Any]:
        if str(envelope.get("request_id") or "") != request_id:
            raise ValueError("Ответ репликации относится к другому запросу")
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        data = base64.b64decode(envelope["ciphertext"], validate=True)
        if len(nonce) != 12 or len(data) > _MAX_RESPONSE_BYTES:
            raise ValueError("Некорректный размер ответа репликации")
        value = json.loads(AESGCM(self._key).decrypt(nonce, data, request_id.encode("ascii")))
        if not isinstance(value, dict):
            raise ValueError("Некорректный формат ответа репликации")
        return value

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="leshy-lan-replication", daemon=True)
        self._thread.start()
        event("cluster_replication_started", mode="chat_memory_stage1")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        # Не увеличиваем latency запуска чатов: синхронизация только в фоне.
        if self._stop.wait(10):
            return
        while not self._stop.is_set():
            try:
                for node in self.runtime.orchestrator.peers():
                    if self._stop.is_set():
                        break
                    host = parse.urlsplit(node.endpoint).hostname or ""
                    if not self._known_peer(node.node_id) or not self._lan_ip(host):
                        continue
                    try:
                        self._pull(node)
                    except Exception as exc:
                        with self._lock:
                            self._last_error = type(exc).__name__ + ": " + str(exc)[:240]
                        problem("cluster_replication_peer_failed", self._last_error, peer=node.node_id)
            except Exception as exc:
                problem("cluster_replication_cycle_failed", type(exc).__name__)
            self._stop.wait(_SYNC_INTERVAL_SECONDS)

    def _cursor(self, node_id: str) -> dict[str, Any]:
        with self.app.storage.connection() as conn:
            row = conn.execute(
                "SELECT session_cursor,message_cursor,memory_cursor "
                "FROM cluster_replication_cursors WHERE peer_id=?", (node_id,),
            ).fetchone()
        return dict(row) if row else {"session_cursor": "", "message_cursor": 0, "memory_cursor": 0}

    def _pull(self, node) -> None:
        cursor = self._cursor(node.node_id)
        request_id = secrets.token_hex(16)
        body = {"action": "pull", "request_id": request_id, "cursor": cursor}
        raw = _canonical(body)
        timestamp = str(int(time.time()))
        url = node.endpoint.rstrip("/") + "/v1/cluster/replicate"
        req = request.Request(
            url, data=raw, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Leshy-Cluster-Id": self.identity.cluster_id,
                "X-Leshy-Cluster-Node": self.identity.node_id,
                "X-Leshy-Cluster-Time": timestamp,
                "X-Leshy-Cluster-Signature": _hmac(
                    self.identity.secret, timestamp.encode("ascii") + b"\n" + raw
                ),
            },
        )
        # Системный HTTP_PROXY не должен перехватывать трафик доверенного LAN.
        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=8) as response:
            encrypted = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(encrypted) > _MAX_RESPONSE_BYTES:
            raise ValueError("Ответ репликации слишком велик")
        payload = self._unseal(json.loads(encrypted), request_id)
        if str(payload.get("source_node") or "") != node.node_id:
            raise ValueError("Узел репликации не совпадает с объявленным")
        count = self._import(node.node_id, payload)
        with self._lock:
            self._last_success = _now()
            self._last_error = ""
            self._imported += count
        if count:
            event("cluster_replication_imported", peer=node.node_id, records=count)

    def handle_request(self, body: dict[str, Any], headers: Any, remote_ip: str) -> dict[str, str]:
        if not self.enabled:
            raise PermissionError("Репликация на этом узле выключена")
        self.runtime.verify_request(body, headers)
        peer = str(headers.get("X-Leshy-Cluster-Node") or "")
        if not self._lan_ip(remote_ip) or not self._known_peer(peer):
            raise PermissionError("Репликация разрешена только доверенному LAN-узлу")
        if str(body.get("action") or "") != "pull":
            raise ValueError("Неизвестный тип запроса репликации")
        request_id = str(body.get("request_id") or "")
        if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise ValueError("Некорректный идентификатор запроса")
        cursor = body.get("cursor")
        if not isinstance(cursor, dict):
            raise ValueError("Отсутствует курсор репликации")
        return self._seal(self._export(cursor), request_id)

    def _export(self, cursor: dict[str, Any]) -> dict[str, Any]:
        session_after = str(cursor.get("session_cursor") or "")[:200]
        message_after = max(0, int(cursor.get("message_cursor") or 0))
        memory_after = max(0, int(cursor.get("memory_cursor") or 0))
        with self.app.storage.connection() as conn:
            sessions = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM sessions WHERE id>? ORDER BY id LIMIT ?",
                    (session_after, _BATCH),
                )
            ]
            messages = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM messages WHERE id>? ORDER BY id LIMIT ?",
                    (message_after, _BATCH),
                )
            ]
            # Для каждого сообщения необходима родительская сессия.
            found = {row["id"] for row in sessions}
            for msg in messages:
                if msg["session_id"] not in found:
                    row = conn.execute("SELECT * FROM sessions WHERE id=?", (msg["session_id"],)).fetchone()
                    if row:
                        sessions.append(dict(row))
                        found.add(msg["session_id"])
                origin = conn.execute(
                    "SELECT origin_node,origin_id FROM cluster_replication_message_origins "
                    "WHERE local_id=? ORDER BY origin_node,origin_id LIMIT 1", (msg["id"],),
                ).fetchone()
                msg["origin_node"] = origin["origin_node"] if origin else self.identity.node_id
                msg["origin_id"] = int(origin["origin_id"]) if origin else int(msg["id"])
            memories = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM memories WHERE id>? ORDER BY id LIMIT ?",
                    (memory_after, _BATCH),
                )
            ]
        next_session = session_after if len(sessions) >= _BATCH else ""
        # Дополнительные сессии для сообщений не должны нарушать курсор страниц.
        if sessions and len([s for s in sessions if s["id"] > session_after]) >= _BATCH:
            next_session = sorted(s["id"] for s in sessions if s["id"] > session_after)[_BATCH - 1]
        return {
            "source_node": self.identity.node_id,
            "sessions": sessions,
            "messages": messages,
            "memories": memories,
            "cursor": {
                "session_cursor": next_session,
                "message_cursor": messages[-1]["id"] if messages else message_after,
                "memory_cursor": memories[-1]["id"] if memories else memory_after,
            },
        }

    def _import(self, peer: str, payload: dict[str, Any]) -> int:
        sessions = payload.get("sessions")
        messages = payload.get("messages")
        memories = payload.get("memories")
        cursor = payload.get("cursor")
        if not all(isinstance(part, list) for part in (sessions, messages, memories)) or not isinstance(cursor, dict):
            raise ValueError("Некорректный пакет репликации")
        if max(len(sessions), len(messages), len(memories)) > _BATCH * 2:
            raise ValueError("Пакет репликации превышает допустимый размер")
        imported = 0
        with self.app.storage.connection() as conn:
            for row in sessions:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("id") or "")[:200]
                if not sid:
                    continue
                existing = conn.execute("SELECT updated_at FROM sessions WHERE id=?", (sid,)).fetchone()
                values = (
                    sid, str(row.get("owner_id") or "standalone")[:200],
                    str(row.get("title") or "")[:500],
                    str(row.get("title_source") or "auto")[:40],
                    int(bool(row.get("pinned"))), int(bool(row.get("archived"))),
                    int(bool(row.get("locked"))), str(row.get("created_at") or _now()),
                    str(row.get("updated_at") or _now()),
                )
                if not existing:
                    conn.execute(
                        "INSERT INTO sessions(id,owner_id,title,title_source,pinned,archived,locked,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", values,
                    )
                    imported += 1
                elif values[-1] > str(existing["updated_at"]):
                    conn.execute(
                        "UPDATE sessions SET owner_id=?,title=?,title_source=?,pinned=?,archived=?,locked=?,updated_at=? "
                        "WHERE id=?", (values[1], values[2], values[3], values[4], values[5], values[6], values[8], sid),
                    )
                    imported += 1
            for row in messages:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("session_id") or "")[:200]
                origin_node = str(row.get("origin_node") or "")[:120]
                origin_id = int(row.get("origin_id") or 0)
                role = str(row.get("role") or "")
                if role not in {"system", "user", "assistant", "tool"} or not sid or not origin_node or origin_id <= 0:
                    continue
                if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                    # Не продвигаем курсор при отсутствующей сессии.
                    raise ValueError("Сообщение пришло без родительской сессии")
                if conn.execute(
                    "SELECT 1 FROM cluster_replication_message_origins WHERE origin_node=? AND origin_id=?",
                    (origin_node, origin_id),
                ).fetchone():
                    continue
                content = str(row.get("content") or "")
                created = str(row.get("created_at") or _now())
                duplicate = conn.execute(
                    "SELECT id FROM messages WHERE session_id=? AND role=? AND content=? AND created_at=? LIMIT 1",
                    (sid, role, content, created),
                ).fetchone()
                if duplicate:
                    local_id = int(duplicate["id"])
                else:
                    local_id = int(conn.execute(
                        "INSERT INTO messages(session_id,role,content,created_at,edited_at) VALUES(?,?,?,?,?)",
                        (sid, role, content, created, row.get("edited_at")),
                    ).lastrowid)
                    imported += 1
                conn.execute(
                    "INSERT INTO cluster_replication_message_origins(origin_node,origin_id,local_id) "
                    "VALUES(?,?,?)", (origin_node, origin_id, local_id),
                )
            for row in memories:
                if not isinstance(row, dict):
                    continue
                fingerprint = str(row.get("fingerprint") or "")
                if not fingerprint:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO memories(owner_id,kind,content,importance,source,created_at,expires_at,fingerprint) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(row.get("owner_id") or "shared")[:200], str(row.get("kind") or "explicit")[:80],
                        str(row.get("content") or ""), float(row.get("importance") or 0.5),
                        row.get("source"), str(row.get("created_at") or _now()), row.get("expires_at"),
                        fingerprint,
                    ),
                )
                imported += max(0, int(cur.rowcount))
            conn.execute(
                "INSERT INTO cluster_replication_cursors(peer_id,session_cursor,message_cursor,memory_cursor,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(peer_id) DO UPDATE SET "
                "session_cursor=excluded.session_cursor,message_cursor=excluded.message_cursor,"
                "memory_cursor=excluded.memory_cursor,updated_at=excluded.updated_at",
                (
                    peer, str(cursor.get("session_cursor") or "")[:200],
                    max(0, int(cursor.get("message_cursor") or 0)),
                    max(0, int(cursor.get("memory_cursor") or 0)), _now(),
                ),
            )
        return imported
