"""Telegram-confirmed browser login for private LAN/IP without OAuth redirects.

The one-time deep-link secret is never used as an authentication assertion:
only a Telegram Bot API update with verified from.id and an inline confirmation
can bind a browser session to that Telegram identity.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any
from urllib import request

from .diagnostics import event, problem
from .storage import utcnow


_TTL = 300
_COOKIE_TTL = 180 * 24 * 60 * 60
_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_browser_login(
    token_hash TEXT PRIMARY KEY,
    callback_hash TEXT NOT NULL UNIQUE,
    web_session_hash TEXT NOT NULL,
    original_owner_id TEXT NOT NULL,
    short_code TEXT NOT NULL,
    telegram_user_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending','confirmed','denied','expired')),
    resolved_owner_id TEXT,
    mode TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tg_browser_login_session
ON telegram_browser_login(web_session_hash,created_at DESC);
"""


class TelegramBrowserLogin:
    """Local, persistent, one-time challenge state shared with the Telegram bot."""

    def __init__(self, identity):
        self.identity = identity
        self.storage = identity.storage
        self._photo_refresh_lock = threading.Lock()
        self._history_reconciliation_lock = threading.Lock()
        self._photo_refresh_last: dict[str, float] = {}
        with self.storage.connection() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _digest(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    def begin(self, session_hash: str) -> dict[str, str | int]:
        now = time.time()
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM owner_web_sessions WHERE session_hash=? AND expires_at>?",
                (session_hash, now),
            ).fetchone()
            if row is None:
                raise PermissionError("Нет действующей Web-сессии")
            conn.execute(
                "UPDATE telegram_browser_login SET status='expired' "
                "WHERE web_session_hash=? AND status='pending'", (session_hash,),
            )
            conn.execute("DELETE FROM telegram_browser_login WHERE expires_at<?", (now - 3600,))
            token = secrets.token_urlsafe(24)
            callback = secrets.token_urlsafe(18)
            hint = secrets.token_hex(3).upper()
            conn.execute(
                "INSERT INTO telegram_browser_login"
                "(token_hash,callback_hash,web_session_hash,original_owner_id,short_code,"
                "status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                (self._digest(token), self._digest(callback), session_hash,
                 str(row["owner_id"]), hint, "pending", now, now + _TTL),
            )
        return {"token": token, "short_code": hint, "expires_in": _TTL}

    def prepare_telegram_confirmation(self, token: str, telegram_user_id: str) -> dict[str, str]:
        value = str(token or "")
        sender = str(telegram_user_id or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{30,60}", value) or not sender.isdecimal():
            raise ValueError("Неверная ссылка для входа")
        now = time.time()
        with self.storage.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT p.callback_hash,p.short_code,p.telegram_user_id,p.web_session_hash "
                "FROM telegram_browser_login p JOIN owner_web_sessions s "
                "ON s.session_hash=p.web_session_hash AND s.owner_id=p.original_owner_id "
                "WHERE p.token_hash=? AND p.status='pending' AND p.expires_at>? "
                "AND s.expires_at>?",
                (self._digest(value), now, now),
            ).fetchone()
            if row is None:
                raise ValueError("Запрос входа истёк или отменён")
            if row["telegram_user_id"] and str(row["telegram_user_id"]) != sender:
                raise PermissionError("Этот запрос уже открыт другим пользователем Telegram")
            # The callback secret must not appear in the Web response or URL.
            # It is generated once when creating the flow, then reproduced from
            # the original secret by storing a dedicated opaque ID in DB.
            callback = self._new_button_token(conn, self._digest(value), sender)
            return {"callback": callback, "short_code": str(row["short_code"])}

    @staticmethod
    def _new_button_token(conn, token_hash: str, user_id: str) -> str:
        callback = secrets.token_urlsafe(18)
        conn.execute(
            "UPDATE telegram_browser_login SET telegram_user_id=?,callback_hash=? "
            "WHERE token_hash=? AND status='pending'",
            (user_id, sha256(callback.encode("utf-8")).hexdigest(), token_hash),
        )
        return callback

    def finish(
        self, callback: str, telegram_user_id: str, sender: dict[str, Any],
        approved: bool,
    ) -> dict[str, str]:
        key = str(callback or "")
        sender_id = str(telegram_user_id or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,45}", key) or not sender_id.isdecimal():
            raise ValueError("Некорректное подтверждение")
        if str(sender.get("id") or "") != sender_id:
            raise PermissionError("Подтверждение не принадлежит отправителю Telegram")
        now = time.time()
        with self.storage.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT p.* FROM telegram_browser_login p "
                "JOIN owner_web_sessions s ON s.session_hash=p.web_session_hash "
                "AND s.owner_id=p.original_owner_id AND s.expires_at>? "
                "WHERE p.callback_hash=? AND p.telegram_user_id=? "
                "AND p.status='pending' AND p.expires_at>?",
                (now, self._digest(key), sender_id, now),
            ).fetchone()
            if row is None:
                raise ValueError("Подтверждение истекло или использовано")
            if not approved:
                conn.execute(
                    "UPDATE telegram_browser_login SET status='denied' WHERE token_hash=?",
                    (str(row["token_hash"]),),
                )
                return {"mode": "denied", "owner_id": ""}
            original_owner = str(row["original_owner_id"])
            existing = conn.execute(
                "SELECT owner_id FROM owner_identity_links "
                "WHERE provider='telegram' AND provider_user_id=?",
                (sender_id,),
            ).fetchone()
            target_owner = str(existing["owner_id"]) if existing else original_owner
            linked_to_current = conn.execute(
                "SELECT provider_user_id FROM owner_identity_links "
                "WHERE provider='telegram' AND owner_id=? LIMIT 1",
                (original_owner,),
            ).fetchone()
            current_provider_user = (
                str(linked_to_current["provider_user_id"])
                if linked_to_current else ""
            )
            if not existing and current_provider_user and current_provider_user != sender_id:
                raise PermissionError("Этот профиль привязан к другому Telegram")
            # Switching accounts on a shared browser must NEVER move the
            # previous user's private chat history into the new account.
            migrate_guest = not current_provider_user and original_owner != target_owner
            name, username, metadata = self._sender_data(sender)
            if not existing:
                conn.execute(
                    "INSERT INTO owner_identity_links"
                    "(provider,provider_user_id,owner_id,display_label,metadata_json,linked_at) "
                    "VALUES('telegram',?,?,?,?,?)",
                    (sender_id, target_owner, name,
                     json.dumps(metadata, ensure_ascii=False), utcnow()),
                )
            else:
                stored = conn.execute(
                    "SELECT metadata_json FROM owner_identity_links "
                    "WHERE provider='telegram' AND provider_user_id=?", (sender_id,),
                ).fetchone()
                try:
                    previous = json.loads(str(stored["metadata_json"] or "{}"))
                except (ValueError, TypeError):
                    previous = {}
                previous.update(metadata)
                conn.execute(
                    "UPDATE owner_identity_links SET display_label=?,metadata_json=? "
                    "WHERE provider='telegram' AND provider_user_id=?",
                    (name, json.dumps(previous, ensure_ascii=False), sender_id),
                )
            conn.execute(
                "UPDATE owner_profiles SET display_name=?,updated_at=? WHERE id=?",
                (name, utcnow(), target_owner),
            )
            conn.execute(
                "UPDATE owner_web_sessions SET owner_id=?,last_seen_at=?,expires_at=? "
                "WHERE session_hash=?",
                (target_owner, utcnow(), now + _COOKIE_TTL, str(row["web_session_hash"])),
            )
            mode = "login" if existing else "linked"
            conn.execute(
                "UPDATE telegram_browser_login "
                "SET status='confirmed',resolved_owner_id=?,mode=? WHERE token_hash=?",
                (target_owner, mode, str(row["token_hash"])),
            )
        # Both existing and first-time Telegram sign-ins retain guest chats.
        # Do not merge someone else's browser identity: session_hash had to
        # match the browser that initiated and confirmed the request.
        try:
            if migrate_guest:
                self.identity._migrate_owner_rows(original_owner, target_owner)
                self.identity._migrate_lenta_profile(original_owner, target_owner)
            # A returning Telegram user can have legacy sessions too (older
            # Bot API/vision paths wrote under telegram:<id>). The old code
            # imported them only for first-time linking, so Web missed chats.
            self.reconcile_linked_telegram_history(target_owner)
        except Exception as exc:
            problem("telegram_browser_login_history_migration_failed",
                    type(exc).__name__, owner_id=target_owner)
        event("telegram_browser_login_confirmed", owner_id=target_owner, mode=mode)
        return {"mode": mode, "owner_id": target_owner}

    @staticmethod
    def _sender_data(sender: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
        first = str(sender.get("first_name") or "").strip()[:80]
        last = str(sender.get("last_name") or "").strip()[:80]
        username = str(sender.get("username") or "").strip().lstrip("@")[:64]
        name = " ".join(p for p in (first, last) if p) or (
            f"@{username}" if username else "Пользователь Telegram"
        )
        metadata = {
            "first_name": first, "last_name": last, "username": username,
            "language_code": str(sender.get("language_code") or "")[:12],
        }
        return name[:200], username, metadata

    def status(self, session_hash: str) -> dict[str, str]:
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT status,short_code,mode,resolved_owner_id,expires_at "
                "FROM telegram_browser_login WHERE web_session_hash=? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(session_hash),),
            ).fetchone()
        if row is None:
            return {"status": "none"}
        state = str(row["status"])
        if state == "pending" and float(row["expires_at"]) <= time.time():
            state = "expired"
        return {
            "status": state,
            "short_code": str(row["short_code"]),
            "mode": str(row["mode"] or ""),
            "owner_id": str(row["resolved_owner_id"] or "") if state == "confirmed" else "",
        }

    def reconcile_linked_telegram_history(self, owner_id: str) -> dict[str, int | bool]:
        """Bring previously stored Telegram conversations into a verified Web profile.

        Only Telegram IDs already verified by the bot and linked to this
        server-side owner UUID are eligible. Never use username, an arbitrary
        owner_id query parameter, or incoming chat text as proof of identity.
        This is an idempotent repair for legacy rows and vision/direct-write
        paths that bypass the normal owner canonicalization.
        """
        owner = str(owner_id or "").strip()
        if not owner:
            return {"linked": False, "sessions_moved": 0}
        with self.storage.connection() as conn:
            links = conn.execute(
                "SELECT provider_user_id FROM owner_identity_links "
                "WHERE provider='telegram' AND owner_id=?",
                (owner,),
            ).fetchall()
        if not links:
            return {"linked": False, "sessions_moved": 0}
        moved = 0
        with self._history_reconciliation_lock:
            for linked in links:
                user_id = str(linked["provider_user_id"] or "")
                if not user_id.isdecimal():
                    continue
                legacy = f"telegram:{user_id}"
                if legacy == owner:
                    continue
                with self.storage.connection() as conn:
                    session_count = int(conn.execute(
                        "SELECT COUNT(*) FROM sessions WHERE owner_id=?",
                        (legacy,),
                    ).fetchone()[0])
                    pending_memories = bool(conn.execute(
                        "SELECT 1 FROM memories WHERE owner_id=? LIMIT 1",
                        (legacy,),
                    ).fetchone())
                    pending_preferences = bool(conn.execute(
                        "SELECT 1 FROM user_preferences WHERE owner_id=? LIMIT 1",
                        (legacy,),
                    ).fetchone())
                if session_count or pending_memories or pending_preferences:
                    self.identity._migrate_owner_rows(legacy, owner)
                    self.identity._migrate_lenta_profile(legacy, owner)
                    moved += session_count
                self.identity._bind_alias(legacy, owner)
        if moved:
            event("telegram_web_history_reconciled", owner_id=owner,
                  sessions_moved=moved)
        return {"linked": True, "sessions_moved": moved}

    def sync_verified_sender(self, user_id: str, sender: dict[str, Any]) -> None:
        """Use only Telegram updates received from the Bot API, not Web fields."""
        user_id = str(user_id).strip()
        if not user_id.isdecimal() or str(sender.get("id") or "") != user_id:
            return
        name, _username, details = self._sender_data(sender)
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT owner_id,metadata_json FROM owner_identity_links "
                "WHERE provider='telegram' AND provider_user_id=?", (user_id,),
            ).fetchone()
            if row is None:
                return
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except (TypeError, ValueError):
                metadata = {}
            metadata.update(details)
            conn.execute(
                "UPDATE owner_identity_links SET display_label=?,metadata_json=? "
                "WHERE provider='telegram' AND provider_user_id=?",
                (name, json.dumps(metadata, ensure_ascii=False), user_id),
            )
            conn.execute(
                "UPDATE owner_profiles SET display_name=?,updated_at=? WHERE id=?",
                (name, utcnow(), str(row["owner_id"])),
            )

    def ensure_photo_refresh(self, telegram, owner_id: str) -> bool:
        """Refresh missing Telegram avatars asynchronously, with bounded retries.

        The login callback and browser reload can race the Bot API image
        request. A failed attempt must not permanently hide the avatar.
        """
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT provider_user_id,metadata_json FROM owner_identity_links "
                "WHERE provider='telegram' AND owner_id=? "
                "ORDER BY linked_at DESC LIMIT 1", (str(owner_id),),
            ).fetchone()
        if row is None:
            return False
        user_id = str(row["provider_user_id"])
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (ValueError, TypeError):
            metadata = {}
        if metadata.get("photo_file_id") or not user_id.isdecimal():
            return False
        now = time.monotonic()
        with self._photo_refresh_lock:
            if now - self._photo_refresh_last.get(user_id, -999999) < 45:
                return False
            self._photo_refresh_last[user_id] = now
            if len(self._photo_refresh_last) > 512:
                cutoff = now - 1800
                self._photo_refresh_last = {
                    k: v for k, v in self._photo_refresh_last.items() if v >= cutoff
                }
        threading.Thread(
            target=self.sync_verified_telegram_profile,
            args=(telegram, user_id),
            name="leshy-telegram-avatar-refresh", daemon=True,
        ).start()
        return True

    def sync_verified_telegram_profile(self, telegram, user_id: str) -> None:
        """Keep photo and public Telegram data, never the bot token or raw file URL."""
        user_id = str(user_id).strip()
        if not user_id.isdecimal():
            return
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT metadata_json,owner_id FROM owner_identity_links "
                "WHERE provider='telegram' AND provider_user_id=?", (user_id,),
            ).fetchone()
        if row is None:
            return
        try:
            result = telegram._api(
                "getUserProfilePhotos", {"user_id": int(user_id), "limit": 1},
                timeout=8,
            ) or {}
            photos = result.get("photos") or []
            variants = photos[0] if photos else []
            file_id = str(variants[-1].get("file_id") or "")[:255] if variants else ""
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except (TypeError, ValueError):
                metadata = {}
            metadata["photo_file_id"] = file_id
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE owner_identity_links SET metadata_json=? "
                    "WHERE provider='telegram' AND provider_user_id=? AND owner_id=?",
                    (json.dumps(metadata, ensure_ascii=False), user_id, str(row["owner_id"])),
                )
        except Exception as exc:
            problem("telegram_profile_photo_refresh_failed", type(exc).__name__)

    def profile(self, owner_id: str, fallback_avatar: dict[str, Any]) -> dict[str, Any]:
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT provider_user_id,display_label,metadata_json "
                "FROM owner_identity_links WHERE provider='telegram' AND owner_id=? "
                "ORDER BY linked_at DESC LIMIT 1",
                (str(owner_id),),
            ).fetchone()
        if row is None:
            return {
                "provider": "local", "name": "", "username": "",
                "avatar_url": str(fallback_avatar.get("url") or ""),
            }
        try:
            meta = json.loads(str(row["metadata_json"] or "{}"))
        except (ValueError, TypeError):
            meta = {}
        name = " ".join(str(meta.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
        file_id = str(meta.get("photo_file_id") or "").strip()
        return {
            "provider": "telegram",
            "name": name or str(row["display_label"] or "").strip() or "Пользователь Telegram",
            "username": str(meta.get("username") or ""),
            "first_name": str(meta.get("first_name") or ""),
            "last_name": str(meta.get("last_name") or ""),
            "language_code": str(meta.get("language_code") or ""),
            "telegram_user_id": str(row["provider_user_id"]),
            "avatar_url": "/v1/identity/telegram/avatar" if file_id else str(fallback_avatar.get("url") or ""),
            "photo_pending": not bool(file_id),
        }

    def avatar(self, telegram, owner_id: str) -> tuple[bytes, str] | None:
        """Only the server accesses the Telegram file endpoint with its bot token."""
        with self.storage.connection() as conn:
            row = conn.execute(
                "SELECT metadata_json,provider_user_id FROM owner_identity_links "
                "WHERE provider='telegram' AND owner_id=? ORDER BY linked_at DESC LIMIT 1",
                (str(owner_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            meta = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError):
            return None
        file_id = str(meta.get("photo_file_id") or "")
        if not file_id or len(file_id) > 255:
            return None
        cache_dir = self.identity.brain_dir / "profile-images"
        key = sha256((str(owner_id) + ":" + file_id).encode("utf-8")).hexdigest()
        cache_path = cache_dir / (key + ".bin")
        if cache_path.is_file():
            raw = cache_path.read_bytes()
            kind = self._image_kind(raw)
            if kind:
                return raw, kind
        try:
            file_result = telegram._api("getFile", {"file_id": file_id}, timeout=8) or {}
            path = str(file_result.get("file_path") or "")
            if (not path or len(path) > 400
                    or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
                    or ".." in path.split("/")):
                return None
            token = telegram._token()
            if not token:
                return None
            url = f"https://api.telegram.org/file/bot{token}/{path}"
            with telegram.proxy.open(request.Request(url, method="GET"), timeout=10) as response:
                raw = response.read(2_000_001)
            kind = self._image_kind(raw)
            if not kind or len(raw) > 2_000_000:
                return None
            cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            cache_path.write_bytes(raw)
            return raw, kind
        except Exception as exc:
            # Do not log the token-bearing URL or exception messages.
            problem("telegram_profile_photo_download_failed", type(exc).__name__)
            return None

    @staticmethod
    def _image_kind(data: bytes) -> str:
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return ""


__all__ = ["TelegramBrowserLogin"]
