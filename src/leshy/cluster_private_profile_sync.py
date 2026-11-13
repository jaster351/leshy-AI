"""Opt-in end-to-end encrypted owner-profile pairing for trusted LAN nodes.

Cluster membership NEVER authenticates a human. Each participating user must
approve a short-lived high-entropy invitation in a live local Web session.
The cluster secret signs envelopes; a distinct per-pair secret encrypts profile
data. Only non-secret UI preferences are replicated in this initial scope.
"""
from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import threading
import time
from urllib import parse, request

from cryptography.fernet import Fernet, InvalidToken

from .diagnostics import event, problem
from .module_settings import module_enabled
from .storage import utcnow


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cluster_private_invites (
  invite_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, code_digest TEXT NOT NULL,
  expires_at REAL NOT NULL, used_at REAL
);
CREATE TABLE IF NOT EXISTS cluster_private_pairs (
  pair_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, remote_owner_id TEXT NOT NULL,
  remote_node_id TEXT NOT NULL, remote_endpoint TEXT NOT NULL,
  sealed_secret TEXT NOT NULL, scopes_json TEXT NOT NULL,
  created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cluster_private_pair_owner
  ON cluster_private_pairs(owner_id,revoked_at);
CREATE TABLE IF NOT EXISTS cluster_private_versions (
  owner_id TEXT NOT NULL, item_key TEXT NOT NULL, source_node TEXT NOT NULL,
  stamp TEXT NOT NULL, PRIMARY KEY(owner_id,item_key)
);
CREATE TABLE IF NOT EXISTS cluster_private_replay_guard (
  pair_id TEXT NOT NULL, sender_node TEXT NOT NULL, nonce TEXT NOT NULL,
  received_at REAL NOT NULL, PRIMARY KEY(pair_id,sender_node,nonce)
);
CREATE INDEX IF NOT EXISTS ix_cluster_private_replay_received
  ON cluster_private_replay_guard(received_at);
"""
_MAX_BODY = 100_000
_ALLOWED_PREFS = frozenset({
    "ui.theme", "ui.language", "ui.compact", "ui.dark_mode",
    "voice.gender", "voice.profile", "voice.rate",
})
_LAN_TIMEOUT = 4.0


def _cipher_for_digest(digest: str) -> Fernet:
    return Fernet(urlsafe_b64encode(bytes.fromhex(digest)))


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_endpoint(value: str) -> str:
    parsed = parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Нужен HTTP(S) адрес LAN-узла без учётных данных")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Укажите только адрес узла и порт")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Требуется IP-адрес локальной сети") from exc
    if (not address.is_private or address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_unspecified or address.is_reserved
            or not port or not 1 <= port <= 65535):
        raise ValueError("Поддерживаются только частные LAN-адреса с TCP-портом")
    return f"{parsed.scheme}://{parsed.netloc}"


def _valid_stamp(value: object) -> str:
    value = str(value or "")
    if len(value) > 50:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.timestamp() > time.time() + 300:
            return ""
    except (ValueError, OverflowError):
        return ""
    return value


def _timestamp_rank(value: object) -> datetime | None:
    """Compare timestamps by UTC instant, never by their string representation."""
    valid = _valid_stamp(value)
    if not valid:
        return None
    try:
        return datetime.fromisoformat(valid.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _incoming_is_newer(incoming_stamp: str, incoming_source: str,
                       current_stamp: str, current_source: str) -> bool:
    """Deterministic LWW with node-ID tie-break for identical UTC instants."""
    incoming_time = _timestamp_rank(incoming_stamp)
    if incoming_time is None:
        return False
    current_time = _timestamp_rank(current_stamp)
    if current_time is None:
        return True
    if incoming_time != current_time:
        return incoming_time > current_time
    return incoming_source > current_source


class _NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Редиректы межузловой синхронизации запрещены")


class ClusterPrivateProfileSync:
    """Private profile sync: explicit paired owners, never shared Knowledge."""

    def __init__(self, app, cluster):
        self.app = app
        self.cluster = cluster
        self.storage = app.storage
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = None
        with self.storage.connection() as conn:
            conn.executescript(_SCHEMA)
        self._vault = Path(app.settings.brain_dir) / "cluster" / "profile-vault.key"
        self._vault.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self._vault.exists():
                temporary = self._vault.with_suffix(".tmp")
                temporary.write_bytes(Fernet.generate_key())
                try:
                    temporary.chmod(0o600)
                except OSError:
                    pass
                temporary.replace(self._vault)
            self._master = Fernet(self._vault.read_bytes().strip())
        self._last_error = ""
        self._last_sync = ""

    def _owner(self, owner_id: str) -> str:
        owner = str(owner_id or "")
        with self.storage.connection() as conn:
            row = conn.execute("SELECT 1 FROM owner_profiles WHERE id=?", (owner,)).fetchone()
        if not row:
            raise PermissionError("Профиль не подтверждён локальной сессией")
        return owner

    def _peer(self, node_id: str, endpoint: str = ""):
        for peer in self.cluster.discovery.peers():
            if peer.node_id == node_id:
                address = _safe_endpoint(peer.endpoint)
                if endpoint and address != _safe_endpoint(endpoint):
                    raise PermissionError("Адрес участника изменился, требуется повторная привязка")
                return peer
        raise PermissionError("Подписанный доверенный LAN-узел не обнаружен")

    def _signed_post(self, endpoint: str, route: str, body: dict) -> dict:
        raw = _json_bytes(body)
        if len(raw) > _MAX_BODY:
            raise ValueError("Пакет превышает предел")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.cluster.identity.secret, timestamp.encode("ascii") + b"\n" + raw, sha256
        ).hexdigest()
        req = request.Request(
            _safe_endpoint(endpoint) + route, data=raw, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Leshy-Cluster-Id": self.cluster.identity.cluster_id,
                "X-Leshy-Cluster-Node": self.cluster.identity.node_id,
                "X-Leshy-Cluster-Time": timestamp,
                "X-Leshy-Cluster-Signature": signature,
            },
        )
        opener = request.build_opener(request.ProxyHandler({}), _NoRedirects())
        with opener.open(req, timeout=_LAN_TIMEOUT) as reply:
            if reply.status != 200:
                raise ConnectionError("Узел отказал в запросе")
            response = reply.read(_MAX_BODY + 1)
            if len(response) > _MAX_BODY:
                raise ValueError("Ответ узла превышает предел")
            decoded = json.loads(response.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("Неверный формат ответа узла")
            return decoded

    def _pair_row(self, pair_id: str, *, owner: str = "", node: str = "") -> dict:
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT * FROM cluster_private_pairs WHERE pair_id=? AND revoked_at IS NULL",
                (str(pair_id or ""),),
            ).fetchone()
        if row is None:
            raise PermissionError("Привязка не найдена или отозвана")
        item = dict(row)
        if owner and item["owner_id"] != owner:
            raise PermissionError("Чужая привязка")
        if node and item["remote_node_id"] != node:
            raise PermissionError("Неверный сопряжённый узел")
        return item

    def _secret_cipher(self, row: dict) -> Fernet:
        raw_key = self._master.decrypt(str(row["sealed_secret"]).encode("ascii"))
        return Fernet(raw_key)

    def _save_pair(self, pair_id: str, owner: str, remote_owner: str,
                   node_id: str, endpoint: str, secret: str) -> None:
        # Never silently remap a trusted node to another local/remote profile.
        # A deliberate change of identity requires revoking the old pair first.
        with self.storage.connection() as conn:
            collision = conn.execute(
                "SELECT 1 FROM cluster_private_pairs WHERE remote_node_id=? "
                "AND revoked_at IS NULL AND (owner_id<>? OR remote_owner_id<>?) "
                "LIMIT 1",
                (node_id, owner, remote_owner),
            ).fetchone()
        if collision:
            raise PermissionError("Узел уже сопряжён с другим профилем; сначала отзовите привязку")
        # An existing pair ID must never be rebound to a different owner.
        with self.storage.connection() as conn:
            other = conn.execute(
                "SELECT owner_id,remote_node_id FROM cluster_private_pairs WHERE pair_id=?",
                (pair_id,),
            ).fetchone()
            if other is not None:
                existing = conn.execute(
                    "SELECT owner_id,remote_owner_id,remote_node_id,remote_endpoint,sealed_secret,"
                    "revoked_at FROM cluster_private_pairs WHERE pair_id=?", (pair_id,)
                ).fetchone()
                if (existing["revoked_at"] is None and existing["owner_id"] == owner
                        and existing["remote_owner_id"] == remote_owner
                        and existing["remote_node_id"] == node_id
                        and existing["remote_endpoint"] == _safe_endpoint(endpoint)
                        and self._master.decrypt(existing["sealed_secret"].encode("ascii"))
                        == secret.encode("ascii")):
                    return  # Lost HTTP response: local retry is idempotent.
                raise PermissionError("Повторная привязка с таким идентификатором запрещена")
            conn.execute(
                "INSERT INTO cluster_private_pairs VALUES(?,?,?,?,?,?,?,?,NULL)",
                (pair_id, owner, remote_owner, node_id, _safe_endpoint(endpoint),
                 self._master.encrypt(secret.encode("ascii")).decode("ascii"),
                 json.dumps(["profile", "ui_preferences"]), utcnow()),
            )

    def invite(self, owner_id: str) -> dict:
        if not module_enabled(self.storage, "distributed-cluster"):
            raise PermissionError("Доверенный кластер выключен")
        owner = self._owner(owner_id)
        code = secrets.token_urlsafe(32)
        invite_id = secrets.token_hex(16)
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM cluster_private_invites WHERE expires_at<?", (time.time(),))
            conn.execute(
                "INSERT INTO cluster_private_invites VALUES(?,?,?,?,NULL)",
                (invite_id, owner, sha256(code.encode("ascii")).hexdigest(), time.time() + 300),
            )
        return {
            "invite_id": invite_id, "code": code,
            "endpoint": _safe_endpoint(self.cluster.local_node.endpoint), "expires_in": 300,
            "scopes": ["profile", "ui_preferences"],
        }

    def accept(self, body: dict, headers) -> dict:
        if not module_enabled(self.storage, "distributed-cluster"):
            raise PermissionError("Доверенный кластер выключен")
        self.cluster.verify_request(body, headers)
        node = str(headers.get("X-Leshy-Cluster-Node") or "")
        peer = self._peer(node)
        invite_id = str(body.get("invite_id") or "")
        with self.storage.connection() as conn:
            invite = conn.execute(
                "SELECT * FROM cluster_private_invites WHERE invite_id=?", (invite_id,)
            ).fetchone()
            if invite is None or float(invite["expires_at"]) <= time.time():
                raise PermissionError("Приглашение истекло или уже использовано")
            owner = str(invite["owner_id"])
            digest = str(invite["code_digest"])
        try:
            payload = json.loads(_cipher_for_digest(digest).decrypt(
                str(body.get("payload") or "").encode("ascii"), ttl=120))
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise PermissionError("Неверный код приглашения") from exc
        if payload.get("node_id") != node or not isinstance(payload.get("owner_id"), str):
            raise PermissionError("Личность сопрягаемого узла не подтверждена")
        remote_owner = str(payload["owner_id"])
        if not (1 <= len(remote_owner) <= 200):
            raise ValueError("Некорректный профиль сопрягаемого узла")
        # Stable, secret-derived ID permits a retry after a lost HTTP response,
        # without allowing a consumed invitation to create another pair.
        pair_id = hmac.new(self.cluster.identity.secret,
                           ("profile-pair:" + invite_id).encode("ascii"), sha256).hexdigest()[:32]
        # A consumed invite is never an authentication credential again.
        # A lost response requires a fresh invite and explicit pairing;
        # returning the existing long-lived key would enable replay.
        if invite["used_at"] is not None:
            raise PermissionError("Приглашение уже использовано; создайте новое")
        key = Fernet.generate_key().decode("ascii")
        # The accepting node must not silently bind an already paired peer to
        # a different account. Check again inside the write transaction below.
        # Consume the invite and persist the pair in the SAME SQLite transaction.
        # A failed insert must roll back consumption of the one-time invitation.
        sealed = self._master.encrypt(key.encode("ascii")).decode("ascii")
        safe_endpoint = _safe_endpoint(peer.endpoint)
        with self._lock:
            with self.storage.connection() as conn:
                changed = conn.execute(
                    "UPDATE cluster_private_invites SET used_at=? WHERE invite_id=? "
                    "AND used_at IS NULL AND expires_at>?",
                    (time.time(), invite_id, time.time()),
                ).rowcount
                if changed != 1:
                    raise PermissionError("Приглашение уже использовано")
                collision = conn.execute(
                    "SELECT 1 FROM cluster_private_pairs WHERE remote_node_id=? "
                    "AND revoked_at IS NULL AND (owner_id<>? OR remote_owner_id<>?) LIMIT 1",
                    (node, owner, remote_owner),
                ).fetchone()
                if collision:
                    raise PermissionError("Узел уже привязан к другому профилю")
                conn.execute(
                    "INSERT INTO cluster_private_pairs VALUES(?,?,?,?,?,?,?,?,NULL)",
                    (pair_id, owner, remote_owner, node, safe_endpoint, sealed,
                     json.dumps(["profile", "ui_preferences"]), utcnow()),
                )
        response = {"pair_id": pair_id, "owner_id": owner,
                    "node_id": self.cluster.identity.node_id, "key": key}
        event("cluster_private_profile_paired", peer=node[:12])
        return {"payload": _cipher_for_digest(digest).encrypt(_json_bytes(response)).decode("ascii")}

    def join(self, owner_id: str, endpoint: str, invite_id: str, code: str) -> dict:
        if not module_enabled(self.storage, "distributed-cluster"):
            raise PermissionError("Доверенный кластер выключен")
        owner = self._owner(owner_id)
        endpoint = _safe_endpoint(endpoint)
        peer = next(
            (p for p in self.cluster.discovery.peers()
             if _safe_endpoint(p.endpoint) == endpoint), None
        )
        if peer is None:
            raise PermissionError("Узел не найден в подписанном LAN-обнаружении")
        if not (16 <= len(code) <= 128 and 10 <= len(invite_id) <= 80):
            raise ValueError("Некорректный код привязки")
        digest = sha256(code.encode("ascii")).hexdigest()
        cipher = _cipher_for_digest(digest)
        inner = {"owner_id": owner, "node_id": self.cluster.identity.node_id}
        body = {"invite_id": invite_id, "payload": cipher.encrypt(_json_bytes(inner)).decode("ascii")}
        answer = self._signed_post(endpoint, "/v1/cluster/private-profile/accept", body)
        try:
            result = json.loads(cipher.decrypt(str(answer["payload"]).encode("ascii"), ttl=120))
        except (InvalidToken, ValueError, KeyError) as exc:
            raise PermissionError("Ответ сопряжения не подтверждён") from exc
        if result.get("node_id") != peer.node_id:
            raise PermissionError("Ответ получен от другого узла")
        try:
            Fernet(str(result["key"]).encode("ascii"))
        except (KeyError, ValueError) as exc:
            raise PermissionError("Недопустимый ключ сопряжения") from exc
        self._save_pair(
            str(result["pair_id"]), owner, str(result["owner_id"]),
            peer.node_id, endpoint, str(result["key"]),
        )
        event("cluster_private_profile_joined", peer=peer.node_id[:12])
        return self.status(owner)

    def status(self, owner_id: str) -> dict:
        owner = self._owner(owner_id)
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT pair_id,remote_node_id,remote_endpoint,scopes_json,created_at "
                "FROM cluster_private_pairs WHERE owner_id=? AND revoked_at IS NULL ORDER BY created_at",
                (owner,),
            ).fetchall()
        return {"enabled": bool(rows), "pair_count": len(rows),
                "pairs": [{**dict(row), "scopes": json.loads(row["scopes_json"])}
                          for row in rows],
                "last_sync": self._last_sync, "last_error": self._last_error}

    def revoke(self, owner_id: str, pair_id: str) -> dict:
        owner = self._owner(owner_id)
        self._pair_row(pair_id, owner=owner)
        with self.storage.connection() as conn:
            conn.execute(
                "UPDATE cluster_private_pairs SET revoked_at=?,sealed_secret='' "
                "WHERE pair_id=? AND owner_id=?",
                (utcnow(), pair_id, owner),
            )
            conn.execute(
                "DELETE FROM cluster_private_replay_guard WHERE pair_id=?", (pair_id,),
            )
        event("cluster_private_profile_revoked", pair=pair_id[:12])
        return self.status(owner)

    def _snapshot(self, owner: str) -> dict:
        with self.storage.connection() as conn:
            profile = conn.execute(
                "SELECT display_name,updated_at FROM owner_profiles WHERE id=?", (owner,)
            ).fetchone()
            rows = conn.execute(
                "SELECT key,value_json,updated_at FROM user_preferences WHERE owner_id=?",
                (owner,),
            ).fetchall()
            versions = {
                row["item_key"]: dict(row) for row in conn.execute(
                    "SELECT * FROM cluster_private_versions WHERE owner_id=?", (owner,)
                ).fetchall()
            }
        values = []
        for row in rows:
            key = str(row["key"])
            raw = str(row["value_json"] or "")
            stamp = _valid_stamp(row["updated_at"])
            if key not in _ALLOWED_PREFS or len(raw) > 4096 or not stamp:
                continue
            try:
                json.loads(raw)
            except ValueError:
                continue
            version = versions.get(key) or {}
            source = (version.get("source_node") if version.get("stamp") == stamp
                      else self.cluster.identity.node_id)
            values.append({"key": key, "value_json": raw, "updated_at": stamp,
                           "source_node": source})
        name_version = versions.get("__profile_name__") or {}
        name_stamp = str(profile["updated_at"]) if profile else ""
        name_source = (name_version.get("source_node") if name_version.get("stamp") == name_stamp
                       else self.cluster.identity.node_id)
        display = {"display_name": str(profile["display_name"] or "")[:200],
                   "updated_at": name_stamp, "source_node": name_source} if profile else {}
        return {"version": 1, "profile": display, "preferences": values}

    def _apply(self, owner: str, doc: dict) -> None:
        if not isinstance(doc, dict) or doc.get("version") != 1:
            raise ValueError("Неверный формат профиля")
        entries = doc.get("preferences") or []
        if not isinstance(entries, list) or len(entries) > len(_ALLOWED_PREFS):
            raise ValueError("Слишком много настроек")
        with self.storage.connection() as conn:
            for item in entries:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                stamp = _valid_stamp(item.get("updated_at"))
                value = str(item.get("value_json") or "")
                source = str(item.get("source_node") or "")
                if key not in _ALLOWED_PREFS or not stamp or not source or len(source) > 128 or len(value) > 4096:
                    continue
                try:
                    json.loads(value)
                except ValueError:
                    continue
                row = conn.execute(
                    "SELECT updated_at FROM user_preferences WHERE owner_id=? AND key=?",
                    (owner, key),
                ).fetchone()
                stored_stamp = str(row["updated_at"]) if row is not None else ""
                version = conn.execute(
                    "SELECT source_node,stamp FROM cluster_private_versions "
                    "WHERE owner_id=? AND item_key=?",
                    (owner, key),
                ).fetchone()
                stored_source = (
                    str(version["source_node"])
                    if version is not None and version["stamp"] == stored_stamp
                    else self.cluster.identity.node_id
                )
                if row is not None and not _incoming_is_newer(
                    stamp, source, stored_stamp, stored_source
                ):
                    continue
                conn.execute(
                    "INSERT INTO user_preferences(owner_id,key,value_json,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(owner_id,key) DO UPDATE SET value_json=excluded.value_json,"
                    "updated_at=excluded.updated_at",
                    (owner, key, value, stamp),
                )
                conn.execute(
                    "INSERT INTO cluster_private_versions(owner_id,item_key,source_node,stamp) "
                    "VALUES(?,?,?,?) ON CONFLICT(owner_id,item_key) DO UPDATE SET "
                    "source_node=excluded.source_node,stamp=excluded.stamp",
                    (owner, key, source, stamp),
                )
            profile = doc.get("profile")
            if isinstance(profile, dict):
                name = profile.get("display_name")
                stamp = _valid_stamp(profile.get("updated_at"))
                source = str(profile.get("source_node") or "")
                if isinstance(name, str) and len(name) <= 200 and stamp and 1 <= len(source) <= 128:
                    current = conn.execute(
                        "SELECT updated_at FROM owner_profiles WHERE id=?", (owner,)
                    ).fetchone()
                    if current is not None:
                        stored_stamp = str(current["updated_at"])
                        version = conn.execute(
                            "SELECT source_node,stamp FROM cluster_private_versions "
                            "WHERE owner_id=? AND item_key='__profile_name__'",
                            (owner,),
                        ).fetchone()
                        stored_source = (
                            str(version["source_node"])
                            if version is not None and version["stamp"] == stored_stamp
                            else self.cluster.identity.node_id
                        )
                    if current is not None and _incoming_is_newer(
                        stamp, source, stored_stamp, stored_source
                    ):
                        conn.execute(
                            "UPDATE owner_profiles SET display_name=?,updated_at=? WHERE id=?",
                            (name, stamp, owner),
                        )
                        conn.execute(
                            "INSERT INTO cluster_private_versions(owner_id,item_key,source_node,stamp) "
                            "VALUES(?,?,?,?) ON CONFLICT(owner_id,item_key) DO UPDATE SET "
                            "source_node=excluded.source_node,stamp=excluded.stamp",
                            (owner, "__profile_name__", source, stamp),
                        )

    def _claim_message(self, pair_id: str, sender_node: str, nonce: object) -> None:
        """Fail closed on replayed encrypted LAN requests across restarts."""
        if (not isinstance(nonce, str) or len(nonce) != 32
                or any(ch not in "0123456789abcdef" for ch in nonce)):
            raise PermissionError("Отсутствует корректный одноразовый идентификатор запроса")
        with self.storage.connection() as conn:
            conn.execute(
                "DELETE FROM cluster_private_replay_guard WHERE received_at<?",
                (time.time() - 600,),
            )
            inserted = conn.execute(
                "INSERT OR IGNORE INTO cluster_private_replay_guard"
                "(pair_id,sender_node,nonce,received_at) VALUES(?,?,?,?)",
                (pair_id, sender_node, nonce, time.time()),
            ).rowcount
            if inserted != 1:
                raise PermissionError("Повторная доставка зашифрованного запроса")

    def receive(self, route: str, body: dict, headers) -> dict:
        if not module_enabled(self.storage, "distributed-cluster"):
            raise PermissionError("Доверенный кластер выключен")
        self.cluster.verify_request(body, headers)
        sender = str(headers.get("X-Leshy-Cluster-Node") or "")
        row = self._pair_row(str(body.get("pair_id") or ""), node=sender)
        self._peer(sender, str(row["remote_endpoint"]))
        cipher = self._secret_cipher(row)
        try:
            inner = json.loads(cipher.decrypt(str(body.get("payload") or "").encode("ascii"), ttl=120))
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise PermissionError("Не удалось проверить ключ владельца") from exc
        if not isinstance(inner, dict):
            raise PermissionError("Некорректный формат зашифрованного профиля")
        if inner.get("owner_id") != row["remote_owner_id"]:
            raise PermissionError("Удалённая личность не совпадает с сопряжением")
        if route not in {"pull", "push"} or inner.get("operation") != route:
            raise PermissionError("Зашифрованное сообщение адресовано другой операции")
        self._claim_message(str(row["pair_id"]), sender, inner.get("nonce"))
        if route == "pull":
            return {"payload": cipher.encrypt(_json_bytes({"request_nonce": inner["nonce"], "snapshot": self._snapshot(row["owner_id"])})).decode("ascii")}
        if route != "push":
            raise ValueError("Неизвестная операция")
        self._apply(str(row["owner_id"]), inner.get("snapshot"))
        return {"ok": True}

    def sync_once(self, owner: str, pair_id: str) -> None:
        if not module_enabled(self.storage, "distributed-cluster"):
            raise PermissionError("Доверенный кластер выключен")
        row = self._pair_row(pair_id, owner=owner)
        self._peer(row["remote_node_id"], row["remote_endpoint"])
        cipher = self._secret_cipher(row)
        base = {"pair_id": pair_id}
        inner = {"owner_id": owner, "operation": "pull", "nonce": secrets.token_hex(16)}
        body = {**base, "payload": cipher.encrypt(_json_bytes(inner)).decode("ascii")}
        reply = self._signed_post(row["remote_endpoint"], "/v1/cluster/private-profile/pull", body)
        remote = json.loads(cipher.decrypt(str(reply["payload"]).encode("ascii"), ttl=120))
        if (not isinstance(remote, dict) or
                not hmac.compare_digest(str(remote.get("request_nonce") or ""), inner["nonce"]) or
                not isinstance(remote.get("snapshot"), dict)):
            raise PermissionError("Ответ не соответствует текущему запросу профиля")
        self._apply(owner, remote["snapshot"])
        outgoing = {"owner_id": owner, "operation": "push", "nonce": secrets.token_hex(16),
                    "snapshot": self._snapshot(owner)}
        pushed = self._signed_post(
            row["remote_endpoint"], "/v1/cluster/private-profile/push",
            {**base, "payload": cipher.encrypt(_json_bytes(outgoing)).decode("ascii")},
        )
        if pushed.get("ok") is not True:
            raise RuntimeError("Удалённый узел не подтвердил обновление")
        self._last_sync = utcnow()
        self._last_error = ""

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="leshy-cluster-private-profile-sync")
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker and self._worker.is_alive() and self._worker is not threading.current_thread():
            self._worker.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(10.0):
            try:
                if not module_enabled(self.storage, "distributed-cluster"):
                    continue
                with self.storage.connection() as conn:
                    pairs = conn.execute(
                        "SELECT pair_id,owner_id FROM cluster_private_pairs WHERE revoked_at IS NULL"
                    ).fetchall()
                for row in pairs:
                    if self._stop.is_set():
                        break
                    try:
                        self.sync_once(str(row["owner_id"]), str(row["pair_id"]))
                    except Exception as exc:
                        self._last_error = type(exc).__name__
                        problem("cluster_private_profile_sync_failed", type(exc).__name__)
            except Exception as exc:
                self._last_error = type(exc).__name__
                problem("cluster_private_profile_sync_loop_failed", type(exc).__name__)


__all__ = ["ClusterPrivateProfileSync", "_safe_endpoint"]
