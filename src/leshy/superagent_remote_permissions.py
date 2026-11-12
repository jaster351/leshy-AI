"""Fail-closed per-node authorization for future SuperAgent remote executors.

A node's network address or possession of its SSH password is never proof of
ownership. The invoking transport must authenticate a human and supply a
server-issued VerifiedActor, not a caller-supplied owner_id.

No command executor is exposed by this module. SSH/RDP implementations must
call require() at the execution boundary and re-check after approvals.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import time

_ALLOWED = frozenset({"inspect", "execute", "update", "reboot", "rdp"})
_NODE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS superagent_remote_nodes (
    node_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    host_fingerprint TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS superagent_remote_permissions (
    node_id TEXT NOT NULL,
    grantee_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (node_id, grantee_id, capability),
    FOREIGN KEY (node_id) REFERENCES superagent_remote_nodes(node_id)
);
"""


@dataclass(frozen=True)
class VerifiedActor:
    """Produced only after a server-side authentication check."""

    owner_id: str
    session_fingerprint: str
    method: str


class RemotePermissionPolicy:
    """Per-user, per-node authorization. Default is always deny."""

    def __init__(self, identity):
        self.identity = identity
        self.storage = identity.storage
        with self.storage.connection() as conn:
            conn.executescript(_SCHEMA)

    def actor_from_web_cookie(self, cookie: str) -> VerifiedActor:
        """Only a valid server-side cookie can create a Web actor.

        A profile UUID, localStorage owner_id, API bearer token or request JSON
        is not sufficient. OAuth provider identity must be verified separately
        before node administration is enabled.
        """
        raw = str(cookie or "")
        if not raw:
            raise PermissionError("Требуется подтверждённая сессия пользователя")
        owner = self.identity.owner_for_web_cookie(raw)
        if not owner:
            raise PermissionError("Сессия пользователя недействительна")
        # The cookie is never returned to a caller or persisted by this module.
        return VerifiedActor(owner, hashlib.sha256(raw.encode("utf-8")).hexdigest(), "web")

    def _check_actor(self, actor: VerifiedActor) -> None:
        if not isinstance(actor, VerifiedActor) or not actor.owner_id or not actor.session_fingerprint:
            raise PermissionError("Личность отправителя не подтверждена")
        if actor.method != "web":
            # Telegram and other clients need their own *server-verified*
            # actor constructors; never trust a claimed Telegram user id.
            raise PermissionError("Канал авторизации не поддерживается")
        with self.storage.connection() as conn:
            session = conn.execute(
                "SELECT 1 FROM owner_web_sessions WHERE session_hash=? "
                "AND owner_id=? AND expires_at>?",
                (actor.session_fingerprint, actor.owner_id, time.time()),
            ).fetchone()
            # An anonymous Web cookie is NOT equivalent to a provider login.
            verified = conn.execute(
                "SELECT 1 FROM owner_identity_links WHERE owner_id=? "
                "AND provider IN ('github','google','microsoft','wordpress') LIMIT 1",
                (actor.owner_id,),
            ).fetchone()
        if session is None or verified is None:
            raise PermissionError("Для удалённого управления требуется вход через подтверждённого провайдера")

    @staticmethod
    def _node(value: str) -> str:
        value = str(value or "").strip()
        if not _NODE_ID.fullmatch(value):
            raise ValueError("Некорректный идентификатор компьютера")
        return value

    @staticmethod
    def _capability(value: str) -> str:
        capability = str(value or "").strip().lower()
        if capability not in _ALLOWED:
            raise PermissionError("Неизвестное или запрещённое удалённое действие")
        return capability

    def register_verified_node(
        self, actor: VerifiedActor, node_id: str, host_fingerprint: str, *,
        enrollment_verified: bool = False,
    ) -> None:
        """Register only following an independent host ownership proof.

        There is deliberately no public HTTP route invoking this method.
        An SSH host key alone does not prove ownership of a server.
        """
        self._check_actor(actor)
        node = self._node(node_id)
        fingerprint = str(host_fingerprint or "").strip()
        if not enrollment_verified or not (16 <= len(fingerprint) <= 256):
            raise PermissionError("Не подтверждены владение узлом и его подлинность")
        with self.storage.connection() as conn:
            existing = conn.execute(
                "SELECT owner_id,host_fingerprint FROM superagent_remote_nodes WHERE node_id=?",
                (node,),
            ).fetchone()
            if existing is not None:
                if existing["owner_id"] != actor.owner_id or not hmac.compare_digest(
                    str(existing["host_fingerprint"]), fingerprint
                ):
                    raise PermissionError("Узел уже зарегистрирован другим владельцем или сменил ключ")
                return
            conn.execute(
                "INSERT INTO superagent_remote_nodes VALUES(?,?,?,?)",
                (node, actor.owner_id, fingerprint, time.time()),
            )

    def grant(
        self, actor: VerifiedActor, node_id: str, grantee_id: str,
        capability: str, *, ttl_seconds: int = 3600,
    ) -> None:
        self._check_actor(actor)
        node = self._node(node_id)
        cap = self._capability(capability)
        grantee = str(grantee_id or "").strip()
        if not grantee or len(grantee) > 200 or not (1 <= ttl_seconds <= 86400):
            raise ValueError("Некорректные параметры разрешения")
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM superagent_remote_nodes WHERE node_id=?", (node,)
            ).fetchone()
            if row is None or row["owner_id"] != actor.owner_id:
                raise PermissionError("Только подтверждённый владелец узла может выдавать доступ")
            # A grant does not confer permission to grant further access.
            conn.execute(
                "INSERT INTO superagent_remote_permissions VALUES(?,?,?,?,?) "
                "ON CONFLICT(node_id,grantee_id,capability) DO UPDATE SET "
                "granted_by=excluded.granted_by,expires_at=excluded.expires_at",
                (node, grantee, cap, actor.owner_id, time.time() + ttl_seconds),
            )

    def revoke(self, actor: VerifiedActor, node_id: str, grantee_id: str, capability: str) -> None:
        self._check_actor(actor)
        node = self._node(node_id)
        cap = self._capability(capability)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM superagent_remote_nodes WHERE node_id=?", (node,)
            ).fetchone()
            if row is None or row["owner_id"] != actor.owner_id:
                raise PermissionError("Только владелец узла может отзывать доступ")
            conn.execute(
                "DELETE FROM superagent_remote_permissions WHERE node_id=? "
                "AND grantee_id=? AND capability=?",
                (node, str(grantee_id), cap),
            )

    def require(self, actor: VerifiedActor, node_id: str, capability: str, *,
                observed_fingerprint: str) -> None:
        """Call immediately before every remote action (also after approval)."""
        self._check_actor(actor)
        node = self._node(node_id)
        cap = self._capability(capability)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id,host_fingerprint FROM superagent_remote_nodes WHERE node_id=?",
                (node,),
            ).fetchone()
            permitted = conn.execute(
                "SELECT 1 FROM superagent_remote_permissions WHERE node_id=? "
                "AND grantee_id=? AND capability=? AND expires_at>?",
                (node, actor.owner_id, cap, time.time()),
            ).fetchone()
        if row is None or not hmac.compare_digest(
            str(row["host_fingerprint"]), str(observed_fingerprint or "")
        ):
            raise PermissionError("Подлинность или регистрация сервера не подтверждена")
        if row["owner_id"] != actor.owner_id and permitted is None:
            raise PermissionError("Нет полномочий управлять этим компьютером")
        # Owner still needs an explicit identity proof, enforced above.
        # A grant to inspect never implies execute/update/reboot/RDP.


__all__ = ["VerifiedActor", "RemotePermissionPolicy"]
