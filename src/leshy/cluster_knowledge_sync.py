from __future__ import annotations

"""Подписанная и зашифрованная двусторонняя синхронизация ОБЩИХ знаний LAN.

Не переносим чаты, личную память, cookies, модели, ключи и профили пользователя.
Внешние утверждения сначала попадают на получателя как непроверенные candidate.
"""

from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import ipaddress
import json
import re
import threading
import time
from urllib import parse, request

from .knowledge import fingerprint_claim
from cryptography.fernet import Fernet, InvalidToken

from .diagnostics import event, problem
from .knowledge import is_fresh
from .module_settings import module_enabled
from .storage import utcnow

_ALLOWED_SOURCE_TYPES = {
    "web", "material-web", "live-search-snippet", "user-read-web",
    "research", "scientific", "academic", "news", "cluster-sync",
}
_MAX_ITEMS = 100
_INTERVAL_SECONDS = 5.0
_MAX_CLAIM_LENGTH = 1600


def _public_source(url: str) -> bool:
    try:
        parsed = parse.urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password or len(url) > 1500:
            return False
        if any(key.lower() in {"token", "key", "apikey", "api_key", "secret", "session", "code", "password"}
               for key, _ in parse.parse_qsl(parsed.query, keep_blank_values=True)):
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host and not host.endswith((".internal", ".lan", ".localhost"))
    except (ValueError, TypeError):
        return False

# Не заменяет анализ всех персональных данных: блокирует только очевидные
# секреты/контакты до отправки в общую базу знаний доверенных узлов.
_SENSITIVE_PATTERNS = (
    re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}(?!\w)"),
    re.compile(r"(?i)(?:\b(?:password|passwd|token|bearer|api[_ -]?key|secret)\b|(?:пароль|токен|секретный[ _-]?ключ))\s*[:=]\s*\S+"),
    re.compile(r"(?<!\w)(?:\+7|8)[\s(.-]*\(?\d{3}\)?[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\w)"),
)


def _safe_public_record(*values: str) -> bool:
    """Блокировать очевидные персональные контакты и секреты в общей записи."""
    return all(
        not pattern.search(str(value or ""))
        for value in values
        for pattern in _SENSITIVE_PATTERNS
    )


class _NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Кластерный узел не должен перенаправлять запросы")


class KnowledgeSyncService:
    """Каждый доверенный узел опрашивает соседей раз в 5 с, курсор — на peer."""

    def __init__(self, app, cluster):
        self.app = app
        self.cluster = cluster
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cursors: dict[str, int] = {}
        self._state = {
            "enabled": True, "state": "waiting", "last_sync": "",
            "last_error": "", "received_total": 0, "sent_total": 0,
            "last_received": 0, "peer_count": 0, "last_peer": "",
        }
        # Ключ шифрования отличается от подписей кластерных задач.
        key = urlsafe_b64encode(sha256(cluster.identity.secret + b":knowledge-sync-v1").digest())
        self._cipher = Fernet(key)

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _patch(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)

    def _cursor_for(self, node_id: str) -> int:
        if node_id in self._cursors:
            return self._cursors[node_id]
        with self.app.storage.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key=?",
                ("cluster.knowledge.cursor." + node_id[:100],),
            ).fetchone()
        try:
            cursor = max(0, int(row["value"])) if row else 0
        except (ValueError, TypeError):
            cursor = 0
        self._cursors[node_id] = cursor
        return cursor

    def _save_cursor(self, node_id: str, value: int) -> None:
        previous = self._cursors.get(node_id, 0)
        if previous == value:
            return
        with self.app.storage.connection() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("cluster.knowledge.cursor." + node_id[:100], str(max(0, value))),
            )
        self._cursors[node_id] = value

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-cluster-knowledge-sync", daemon=True)
        self._thread.start()
        event("cluster_knowledge_sync_started", interval_seconds=_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _export(self, cursor: int) -> dict:
        # Cursor возвращается даже при пропуске строки: исключённая запись не
        # должна бесконечно блокировать остальную репликацию.
        cursor = max(0, int(cursor))
        with self.app.storage.connection() as conn:
            max_row = conn.execute("SELECT COALESCE(MAX(id),0) AS last_id FROM knowledge").fetchone()
            max_id = int(max_row["last_id"])
            if cursor > max_id:
                cursor = 0  # база данных источника была восстановлена с нуля
            rows = conn.execute(
                "SELECT id,topic,claim,source_url,source_title,source_type,"
                "data_class,checked_at,ttl_seconds,captured_at,status "
                "FROM knowledge WHERE id>? ORDER BY id LIMIT ?",
                (cursor, _MAX_ITEMS),
            ).fetchall()
        items: list[dict] = []
        next_cursor = cursor
        for record in rows:
            row = dict(record)
            next_cursor = int(row["id"])
            claim = str(row.get("claim") or "").strip()
            source = str(row.get("source_url") or "").strip()
            if row.get("status") not in {"candidate", "trusted"}:
                continue
            if row.get("source_type") not in _ALLOWED_SOURCE_TYPES:
                continue
            if not 20 <= len(claim) <= _MAX_CLAIM_LENGTH or not _public_source(source):
                continue
            if not _safe_public_record(claim, row.get("topic"), row.get("source_title"), source):
                continue
            if not is_fresh(row):
                continue
            items.append({
                "topic": str(row.get("topic") or "")[:500],
                "claim": claim,
                "source_url": source,
                "source_title": str(row.get("source_title") or "")[:400],
                "data_class": "dynamic" if row.get("data_class") == "dynamic" else "static",
                "checked_at": row.get("checked_at"),
                "ttl_seconds": row.get("ttl_seconds"),
            })
        return {"items": items, "cursor": next_cursor, "total_exportable": len(items)}

    def handle_pull(self, body: dict, headers) -> dict:
        self.cluster.verify_request(body, headers)
        if not module_enabled(self.app.storage, "distributed-cluster") or not bool(getattr(self.cluster, "_autodiscovery_configured", True)):
            raise PermissionError("Кластер или автообнаружение отключены")
        encrypted = str(body.get("payload") or "")
        if len(encrypted) > 20_000:
            raise ValueError("Слишком большой запрос синхронизации")
        try:
            query = json.loads(self._cipher.decrypt(encrypted.encode("ascii"), ttl=120))
            cursor = int(query.get("cursor") or 0)
            if cursor < 0:
                raise ValueError("Некорректный курсор")
        except (InvalidToken, ValueError, TypeError, UnicodeError) as exc:
            raise PermissionError("Не удалось проверить зашифрованный запрос") from exc
        page = self._export(cursor)
        payload = self._cipher.encrypt(json.dumps(page, ensure_ascii=False).encode("utf-8"))
        self._patch(sent_total=self.status()["sent_total"] + len(page["items"]))
        return {"payload": payload.decode("ascii")}

    def _pull_peer(self, peer, cursor: int) -> dict:
        request_data = {"cursor": cursor}
        token = self._cipher.encrypt(json.dumps(request_data).encode("utf-8")).decode("ascii")
        body = {"payload": token}
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.cluster.identity.secret, timestamp.encode("ascii") + b"\n" + raw, "sha256",
        ).hexdigest()
        target = parse.urlsplit(peer.endpoint)
        try:
            address = ipaddress.ip_address(target.hostname or "")
        except ValueError as exc:
            raise ValueError("Ожидается IP-адрес узла локальной сети") from exc
        if target.scheme not in {"http", "https"} or (
            not address.is_private or address.is_loopback or address.is_link_local
        ):
            raise ValueError("Репликация разрешена только на доверенные LAN-адреса")
        req = request.Request(
            peer.endpoint.rstrip("/") + "/v1/cluster/knowledge-sync/pull",
            data=raw, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Leshy-Cluster-Id": self.cluster.identity.cluster_id,
                "X-Leshy-Cluster-Node": self.cluster.identity.node_id,
                "X-Leshy-Cluster-Time": timestamp,
                "X-Leshy-Cluster-Signature": signature,
            },
        )
        # Интервал ограничивает задержку, синхронизация не блокирует чат.
        opener = request.build_opener(request.ProxyHandler({}), _NoRedirects())
        with opener.open(req, timeout=3.0) as response:
            if response.status != 200:
                raise RuntimeError("Сосед отклонил синхронизацию")
            response_data = json.loads(response.read(300_000))
        ciphertext = str(response_data.get("payload") or "")
        if len(ciphertext) > 250_000:
            raise ValueError("Превышен размер ответа синхронизации")
        return json.loads(self._cipher.decrypt(ciphertext.encode("ascii"), ttl=120))

    def _import_page(self, page: dict) -> int:
        rows = page.get("items") or []
        if not isinstance(rows, list) or len(rows) > _MAX_ITEMS:
            raise ValueError("Некорректный пакет знаний")
        added = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            claim = str(row.get("claim") or "").strip()
            source = str(row.get("source_url") or "").strip()
            topic = str(row.get("topic") or "").strip()[:500]
            if not (20 <= len(claim) <= _MAX_CLAIM_LENGTH) or not topic or not _public_source(source):
                continue
            if not _safe_public_record(claim, topic, row.get("source_title"), source):
                continue
            data_class = row.get("data_class") if row.get("data_class") in {"static", "dynamic"} else "static"
            if data_class == "dynamic":
                checked_at = str(row.get("checked_at") or "")
                ttl = min(86_400, max(0, int(row.get("ttl_seconds") or 0)))
                if not is_fresh({"data_class": "dynamic", "checked_at": checked_at, "ttl_seconds": ttl}):
                    continue
            else:
                checked_at, ttl = None, None
            # Не копируем статус trusted: получатель сохраняет candidate,
            # подтверждение должно быть независимым.
            with self.app.storage.connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM knowledge WHERE fingerprint=? AND source_url=? LIMIT 1",
                    (fingerprint_claim(claim), source),
                ).fetchone()
            if exists:
                continue
            self.app.knowledge.add_candidate(
                topic=topic, claim=claim, source_url=source,
                source_title=str(row.get("source_title") or "")[:400],
                source_type="cluster-sync", data_class=data_class,
                checked_at=checked_at, ttl_seconds=ttl,
                metadata_json={"imported_from_cluster": True, "unverified": True},
            )
            added += 1
        return added

    def _run(self) -> None:
        while not self._stop.wait(_INTERVAL_SECONDS):
            try:
                cluster = self.cluster
                if not module_enabled(self.app.storage, "distributed-cluster") or not bool(getattr(cluster, "_autodiscovery_configured", True)):
                    self._patch(state="disabled", peer_count=0)
                    continue
                peers = [
                    peer for peer in cluster.orchestrator.peers()
                    if peer.node_id != cluster.identity.node_id
                ]
                self._patch(peer_count=len(peers))
                if not peers:
                    self._patch(state="waiting", last_received=0)
                    continue
                total = 0
                for peer in peers:
                    if self._stop.is_set():
                        break
                    cursor = self._cursor_for(peer.node_id)
                    stage = "pull"
                    try:
                        page = self._pull_peer(peer, cursor)
                        stage = "cursor"
                        next_cursor = max(0, int(page.get("cursor") or 0))
                        stage = "import"
                        received = self._import_page(page)
                        stage = "save_cursor"
                        self._save_cursor(peer.node_id, next_cursor)
                        total += received
                        self._patch(
                            state="synchronized", last_error="", last_sync=utcnow(),
                            last_peer=peer.node_id[:12], last_received=received,
                        )
                        if received:
                            event("cluster_knowledge_sync_imported", received=received, peer=peer.node_id[:12])
                    except Exception as exc:
                        self._patch(state="error", last_error=type(exc).__name__ + ": " + str(exc)[:150])
                        # Log only structured error types/codes: exception text may contain URLs or secrets.
                        reason = getattr(exc, "reason", None)
                        problem(
                            "cluster_knowledge_sync_failed",
                            type(exc).__name__,
                            peer=peer.node_id[:12],
                            stage=stage,
                            reason_type=type(reason).__name__ if reason is not None else "",
                            errno=getattr(reason, "errno", None) if isinstance(getattr(reason, "errno", None), int) else None,
                        )
                if total:
                    self._patch(received_total=self.status()["received_total"] + total)
            except Exception as exc:
                self._patch(state="error", last_error=type(exc).__name__)
                problem("cluster_knowledge_sync_loop_failed", type(exc).__name__)


__all__ = ["KnowledgeSyncService"]
