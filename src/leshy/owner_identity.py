from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import shutil
import string
import time
from typing import Any
import uuid

from .diagnostics import event, problem
from .storage import utcnow


_COOKIE_TTL_SECONDS = 180 * 24 * 60 * 60
_LINK_TTL_SECONDS = 5 * 60
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_profiles(
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_identity_aliases(
    alias TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_owner_identity_alias_owner ON owner_identity_aliases(owner_id);
CREATE TABLE IF NOT EXISTS owner_identity_links(
    provider TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    display_label TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    linked_at TEXT NOT NULL,
    PRIMARY KEY(provider,provider_user_id)
);
CREATE INDEX IF NOT EXISTS ix_owner_identity_links_owner ON owner_identity_links(owner_id,provider);
CREATE TABLE IF NOT EXISTS owner_web_sessions(
    session_hash TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_owner_web_sessions_owner ON owner_web_sessions(owner_id,expires_at);
CREATE TABLE IF NOT EXISTS owner_link_tokens(
    token_hash TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    target_owner_id TEXT NOT NULL,
    web_session_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE INDEX IF NOT EXISTS ix_owner_link_tokens_expiry ON owner_link_tokens(expires_at,used_at);
CREATE TABLE IF NOT EXISTS owner_profile_avatars(
    owner_id TEXT PRIMARY KEY,
    gravatar_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS owner_browser_pairings(
    token_hash TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    originating_session_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);
CREATE INDEX IF NOT EXISTS ix_owner_browser_pairings_expiry
ON owner_browser_pairings(expires_at,consumed_at);
"""


def _safe_key(value: str) -> str:
    raw = str(value or "").strip()[:200]
    digest = sha256(raw.encode("utf-8")).hexdigest()[:24]
    prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw).strip("-._")[:32] or "user"
    return f"{prefix}-{digest}"


class OwnerIdentityService:
    """Единая локальная идентичность владельцев Лешего.

    Telegram-пользователь без Web-привязки продолжает жить под временной
    идентичностью ``telegram:<id>``. После одноразового подтверждения его канал
    привязывается к внутреннему owner UUID. Если Telegram уже был привязан к
    другому Web-профилю, подтверждение на новом Web-сеансе работает как вход:
    Web-сеанс переключается на уже существующий owner UUID.
    """

    cookie_name = "leshy_owner_session"

    def __init__(self, storage, brain_dir: Path | str):
        self.storage = storage
        self.brain_dir = Path(brain_dir)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.storage.connection() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("DELETE FROM owner_web_sessions WHERE expires_at<?", (time.time(),))
            conn.execute("DELETE FROM owner_link_tokens WHERE expires_at<? OR used_at IS NOT NULL", (time.time(),))
            conn.execute("DELETE FROM owner_browser_pairings WHERE expires_at<? OR consumed_at IS NOT NULL", (time.time(),))

    @staticmethod
    def _hash_secret(value: str) -> str:
        return sha256(str(value or "").encode("utf-8")).hexdigest()

    def _new_owner(self, display_name: str = "") -> str:
        owner_id = str(uuid.uuid4())
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO owner_profiles(id,display_name,created_at,updated_at) VALUES(?,?,?,?)",
                (owner_id, str(display_name or "")[:200], now, now),
            )
        event("owner_identity_created", owner_id=owner_id)
        return owner_id

    def _profile_exists(self, owner_id: str) -> bool:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT 1 FROM owner_profiles WHERE id=?", (str(owner_id),)).fetchone()
        return row is not None

    def _alias_owner(self, alias: str) -> str:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT owner_id FROM owner_identity_aliases WHERE alias=?", (str(alias),)).fetchone()
        return str(row["owner_id"]) if row is not None else ""

    def _bind_alias(self, alias: str, owner_id: str) -> None:
        value = str(alias or "").strip()[:200]
        if not value:
            return
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO owner_identity_aliases(alias,owner_id,created_at) VALUES(?,?,?) "
                "ON CONFLICT(alias) DO UPDATE SET owner_id=excluded.owner_id",
                (value, owner_id, utcnow()),
            )

    def _legacy_web_claimed(self) -> bool:
        with self.storage.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='owner_identity.legacy_web_claimed'").fetchone()
        return bool(row and str(row["value"]) == "1")

    def _mark_legacy_web_claimed(self) -> None:
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('owner_identity.legacy_web_claimed','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )

    def _create_web_owner(self, presented_owner_id: str) -> str:
        presented = str(presented_owner_id or "").strip()[:200]
        # Только первый браузер после обновления забирает старые данные web:local.
        if presented == "web:local" and not self._legacy_web_claimed():
            owner_id = self._new_owner("Основной профиль")
            self._migrate_owner_rows("web:local", owner_id)
            self._bind_alias("web:local", owner_id)
            self._migrate_lenta_profile("web:local", owner_id)
            self._mark_legacy_web_claimed()
            event("owner_identity_legacy_web_claimed", owner_id=owner_id)
            return owner_id
        return self._new_owner("Профиль Лешего")

    def ensure_web_session(self, raw_cookie: str, presented_owner_id: str = "web:local") -> tuple[str, str | None, str]:
        """Вернуть (owner_id, new_cookie_or_none, session_hash)."""
        self._ensure_schema()
        cookie = str(raw_cookie or "").strip()
        now_epoch = time.time()
        if cookie:
            session_hash = self._hash_secret(cookie)
            with self.storage.connection() as conn:
                row = conn.execute(
                    "SELECT owner_id FROM owner_web_sessions WHERE session_hash=? AND expires_at>?",
                    (session_hash, now_epoch),
                ).fetchone()
                if row is not None:
                    owner_id = str(row["owner_id"])
                    conn.execute(
                        "UPDATE owner_web_sessions SET last_seen_at=?,expires_at=? WHERE session_hash=?",
                        (utcnow(), now_epoch + _COOKIE_TTL_SECONDS, session_hash),
                    )
                    return owner_id, None, session_hash

        owner_id = self._create_web_owner(presented_owner_id)
        token = secrets.token_urlsafe(32)
        session_hash = self._hash_secret(token)
        now = utcnow()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO owner_web_sessions(session_hash,owner_id,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?)",
                (session_hash, owner_id, now, now, now_epoch + _COOKIE_TTL_SECONDS),
            )
        event("owner_identity_web_session_created", owner_id=owner_id)
        return owner_id, token, session_hash

    def owner_for_web_cookie(self, raw_cookie: str) -> str:
        cookie = str(raw_cookie or "").strip()
        if not cookie:
            return ""
        digest = self._hash_secret(cookie)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM owner_web_sessions WHERE session_hash=? AND expires_at>?",
                (digest, time.time()),
            ).fetchone()
        return str(row["owner_id"]) if row is not None else ""

    def resolve_telegram(self, telegram_user_id: str | int) -> str:
        user_id = str(telegram_user_id or "").strip()
        if not user_id:
            raise ValueError("Telegram user_id обязателен")
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM owner_identity_links WHERE provider='telegram' AND provider_user_id=?",
                (user_id,),
            ).fetchone()
        if row is not None:
            return str(row["owner_id"])
        return f"telegram:{user_id}"

    def status(self, owner_id: str) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        if not self._profile_exists(owner):
            raise ValueError("Web-профиль не найден")
        with self.storage.connection() as conn:
            profile = conn.execute(
                "SELECT id,display_name,created_at,updated_at FROM owner_profiles WHERE id=?",
                (owner,),
            ).fetchone()
            rows = conn.execute(
                "SELECT provider_user_id,display_label,metadata_json,linked_at FROM owner_identity_links "
                "WHERE owner_id=? AND provider='telegram' ORDER BY linked_at",
                (owner,),
            ).fetchall()
        telegram: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except Exception:
                metadata = {}
            telegram.append({
                "user_id": str(row["provider_user_id"]),
                "label": str(row["display_label"] or ""),
                "username": str((metadata or {}).get("username") or ""),
                "linked_at": str(row["linked_at"]),
            })
        return {
            "owner_id": str(profile["id"]),
            "display_name": str(profile["display_name"] or "Профиль Лешего"),
            "created_at": str(profile["created_at"]),
            "telegram": telegram,
            "telegram_linked": bool(telegram),
            "web_session_server_side": True,
            "avatar": self.gravatar_for_owner(owner),
        }

    def gravatar_for_owner(self, owner_id: str) -> dict[str, Any]:
        """External avatar is optional; a hash is not proof of ownership of an email."""
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT gravatar_hash FROM owner_profile_avatars WHERE owner_id=?",
                (str(owner_id),),
            ).fetchone()
        if row is None:
            return {"provider": "local", "url": "", "external_request": False}
        digest = str(row["gravatar_hash"])
        return {
            "provider": "gravatar",
            "url": f"https://gravatar.com/avatar/{digest}?s=96&d=404",
            "external_request": True,
        }

    def configure_gravatar(self, owner_id: str, email: str) -> dict[str, Any]:
        """Store only the SHA256 identifier, never raw email. Not a login method."""
        owner = str(owner_id or "").strip()
        if not self._profile_exists(owner):
            raise PermissionError("Нет подтверждённого профиля")
        value = str(email or "").strip().lower()
        if not value or len(value) > 254 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", value
        ):
            raise ValueError("Укажите корректный адрес email для Gravatar")
        digest = sha256(value.encode("utf-8")).hexdigest()
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO owner_profile_avatars(owner_id,gravatar_hash,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(owner_id) DO UPDATE SET "
                "gravatar_hash=excluded.gravatar_hash,updated_at=excluded.updated_at",
                (owner, digest, utcnow()),
            )
        return self.gravatar_for_owner(owner)

    def clear_gravatar(self, owner_id: str) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        if not self._profile_exists(owner):
            raise PermissionError("Нет подтверждённого профиля")
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM owner_profile_avatars WHERE owner_id=?", (owner,))
        return self.gravatar_for_owner(owner)

    def create_browser_pairing(self, owner_id: str, session_hash: str) -> dict[str, Any]:
        """A signed-in Web browser issues a short-lived 128-bit one-time login code."""
        owner = str(owner_id or "").strip()
        session = str(session_hash or "").strip()
        now = time.time()
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM owner_web_sessions WHERE session_hash=? AND expires_at>?",
                (session, now),
            ).fetchone()
            if row is None or str(row["owner_id"]) != owner:
                raise PermissionError("Сеанс профиля не подтверждён")
            conn.execute(
                "DELETE FROM owner_browser_pairings WHERE expires_at<? OR consumed_at IS NOT NULL",
                (now,),
            )
            code = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO owner_browser_pairings(token_hash,owner_id,originating_session_hash,expires_at) "
                "VALUES(?,?,?,?)",
                (self._hash_secret(code), owner, session, now + _LINK_TTL_SECONDS),
            )
        return {"code": code, "expires_in": _LINK_TTL_SECONDS}

    def consume_browser_pairing(self, code: str, session_hash: str) -> dict[str, Any]:
        """Switch only the presented browser session to the verified owner."""
        raw = str(code or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", raw):
            raise ValueError("Неверный формат кода")
        digest = self._hash_secret(raw)
        session = str(session_hash or "").strip()
        now = time.time()
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT p.owner_id, p.originating_session_hash FROM owner_browser_pairings p "
                "JOIN owner_web_sessions s ON s.session_hash=p.originating_session_hash "
                "WHERE p.token_hash=? AND p.consumed_at IS NULL AND p.expires_at>? "
                "AND s.expires_at>? AND s.owner_id=p.owner_id",
                (digest, now, now),
            ).fetchone()
            if row is None:
                raise ValueError("Код истёк или уже использован")
            target = conn.execute(
                "SELECT owner_id FROM owner_web_sessions WHERE session_hash=? AND expires_at>?",
                (session, now),
            ).fetchone()
            if target is None:
                raise PermissionError("Нет действующей сессии браузера")
            owner = str(row["owner_id"])
            updated = conn.execute(
                "UPDATE owner_browser_pairings SET consumed_at=? "
                "WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?",
                (now, digest, now),
            )
            if updated.rowcount != 1:
                raise ValueError("Код уже использован")
            conn.execute(
                "UPDATE owner_web_sessions SET owner_id=?,last_seen_at=? WHERE session_hash=?",
                (owner, utcnow(), session),
            )
        event("owner_identity_browser_paired", owner_id=owner)
        return {"owner_id": owner, "mode": "login"}

    def create_telegram_link(self, owner_id: str, web_session_hash: str) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        if not self._profile_exists(owner):
            raise ValueError("Web-профиль не найден")
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        token_hash = self._hash_secret(code)
        now_epoch = time.time()
        with self.storage.connection() as conn:
            conn.execute("DELETE FROM owner_link_tokens WHERE expires_at<? OR used_at IS NOT NULL", (now_epoch,))
            conn.execute(
                "INSERT INTO owner_link_tokens(token_hash,provider,target_owner_id,web_session_hash,created_at,expires_at,used_at) "
                "VALUES(?,?,?,?,?,?,NULL)",
                (token_hash, "telegram", owner, str(web_session_hash or ""), utcnow(), now_epoch + _LINK_TTL_SECONDS),
            )
        event("owner_identity_telegram_link_created", owner_id=owner)
        return {"code": code, "expires_in": _LINK_TTL_SECONDS}

    def consume_telegram_link(
        self,
        code: str,
        telegram_user_id: str | int,
        *,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ) -> dict[str, Any]:
        value = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
        if not value:
            raise ValueError("Код привязки пуст")
        user_id = str(telegram_user_id or "").strip()
        if not user_id:
            raise ValueError("Telegram user_id обязателен")
        digest = self._hash_secret(value)
        now_epoch = time.time()
        with self.storage.connection() as conn:
            token = conn.execute(
                "SELECT target_owner_id,web_session_hash,expires_at,used_at FROM owner_link_tokens "
                "WHERE token_hash=? AND provider='telegram'",
                (digest,),
            ).fetchone()
            if token is None or token["used_at"] is not None or float(token["expires_at"] or 0) <= now_epoch:
                raise ValueError("Код привязки недействителен или истёк")
            target_owner = str(token["target_owner_id"])
            web_session_hash = str(token["web_session_hash"] or "")
            existing = conn.execute(
                "SELECT owner_id FROM owner_identity_links WHERE provider='telegram' AND provider_user_id=?",
                (user_id,),
            ).fetchone()
            existing_owner = str(existing["owner_id"]) if existing is not None else ""

        if existing_owner and existing_owner != target_owner:
            # Telegram уже подтверждал другой профиль: это безопасный вход в него
            # с нового браузера, а не перенос Telegram на пустой новый профиль.
            self._migrate_owner_rows(target_owner, existing_owner)
            self._migrate_lenta_profile(target_owner, existing_owner)
            target_owner = existing_owner
            if web_session_hash:
                with self.storage.connection() as conn:
                    conn.execute(
                        "UPDATE owner_web_sessions SET owner_id=?,last_seen_at=?,expires_at=? WHERE session_hash=?",
                        (target_owner, utcnow(), now_epoch + _COOKIE_TTL_SECONDS, web_session_hash),
                    )
            mode = "login"
        else:
            temporary_owner = f"telegram:{user_id}"
            self._migrate_owner_rows(temporary_owner, target_owner)
            self._migrate_lenta_profile(temporary_owner, target_owner)
            label = " ".join(part for part in (str(first_name or "").strip(), str(last_name or "").strip()) if part).strip()
            if username:
                label = f"{label} (@{username})" if label else f"@{username}"
            metadata = {"username": str(username or "")[:100]}
            with self.storage.connection() as conn:
                conn.execute(
                    "INSERT INTO owner_identity_links(provider,provider_user_id,owner_id,display_label,metadata_json,linked_at) "
                    "VALUES('telegram',?,?,?,?,?) "
                    "ON CONFLICT(provider,provider_user_id) DO UPDATE SET owner_id=excluded.owner_id,display_label=excluded.display_label,metadata_json=excluded.metadata_json,linked_at=excluded.linked_at",
                    (user_id, target_owner, label[:200], json.dumps(metadata, ensure_ascii=False), utcnow()),
                )
            self._bind_alias(temporary_owner, target_owner)
            mode = "linked"

        with self.storage.connection() as conn:
            conn.execute("UPDATE owner_link_tokens SET used_at=? WHERE token_hash=?", (time.time(), digest))
        event("owner_identity_telegram_confirmed", owner_id=target_owner, telegram_user_id=user_id, mode=mode)
        return {"owner_id": target_owner, "telegram_user_id": user_id, "mode": mode}

    def unlink_telegram(self, owner_id: str, telegram_user_id: str | int) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        user_id = str(telegram_user_id or "").strip()
        if not owner or not user_id:
            raise ValueError("Нужны owner_id и Telegram user_id")
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM owner_identity_links WHERE provider='telegram' AND provider_user_id=?",
                (user_id,),
            ).fetchone()
            if row is None or str(row["owner_id"]) != owner:
                raise PermissionError("Этот Telegram не принадлежит текущему Web-профилю")
            conn.execute(
                "DELETE FROM owner_identity_links WHERE provider='telegram' AND provider_user_id=?",
                (user_id,),
            )
            conn.execute("DELETE FROM owner_identity_aliases WHERE alias=? AND owner_id=?", (f"telegram:{user_id}", owner))
            # Telegram после отвязки снова получает собственные диалоги. Выделяем
            # только Telegram-сессии; общая память владельца назад не дробится.
            conn.execute(
                "UPDATE sessions SET owner_id=? WHERE owner_id=? AND id LIKE ?",
                (f"telegram:{user_id}", owner, f"telegram:%:{user_id}"),
            )
        event("owner_identity_telegram_unlinked", owner_id=owner, telegram_user_id=user_id)
        return self.status(owner)

    def _migrate_owner_rows(self, source_owner: str, target_owner: str) -> None:
        source = str(source_owner or "").strip()
        target = str(target_owner or "").strip()
        if not source or not target or source == target:
            return
        try:
            with self.storage.connection() as conn:
                tables = [str(row["name"]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for table in tables:
                    if table.startswith("sqlite_") or table.startswith("owner_"):
                        continue
                    columns = {str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
                    if "owner_id" not in columns:
                        continue
                    if table == "user_preferences":
                        conn.execute(
                            "INSERT OR IGNORE INTO user_preferences(owner_id,key,value_json,updated_at) "
                            "SELECT ?,key,value_json,updated_at FROM user_preferences WHERE owner_id=?",
                            (target, source),
                        )
                        conn.execute("DELETE FROM user_preferences WHERE owner_id=?", (source,))
                        continue
                    if table == "lenta_conversation_state":
                        # Нельзя переносить ожидающее подтверждение заказа между
                        # идентичностями: после входа пользователь начнёт заново.
                        conn.execute("DELETE FROM lenta_conversation_state WHERE owner_id=?", (source,))
                        continue
                    conn.execute(f'UPDATE OR IGNORE "{table}" SET owner_id=? WHERE owner_id=?', (target, source))
            event("owner_identity_data_migrated", source_owner=source, target_owner=target)
        except Exception as exc:
            problem("owner_identity_data_migration_failed", str(exc), source_owner=source, target_owner=target)
            raise

    def _migrate_lenta_profile(self, source_owner: str, target_owner: str) -> None:
        if not source_owner or not target_owner or source_owner == target_owner:
            return
        root = self.brain_dir / "browser" / "profiles" / "lenta"
        source = root / _safe_key(source_owner)
        target = root / _safe_key(target_owner)
        if not source.exists():
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.move(str(source), str(target))
                event("owner_identity_lenta_profile_migrated", source_owner=source_owner, target_owner=target_owner)
            else:
                # Уже авторизованный целевой профиль важнее временного Telegram/Web.
                event("owner_identity_lenta_profile_kept_target", source_owner=source_owner, target_owner=target_owner)
        except Exception as exc:
            problem("owner_identity_lenta_profile_migration_failed", str(exc), source_owner=source_owner, target_owner=target_owner)


__all__ = ["OwnerIdentityService"]
