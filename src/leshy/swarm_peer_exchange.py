from __future__ import annotations

"""Bounded peer address book for the public Leshy discovery pilot.

Peers are *untrusted hints*, not authenticated computation workers. No private
data or task payload is sent by this module. A peer must explicitly publish a
public HTTPS endpoint to be dialable from the internet.
"""

import json
from pathlib import Path
import re
import threading
import time

from .swarm_bootstrap import _seed_url


_PEER_TTL_SECONDS = 6 * 3600
_MAX_PEERS = 64
_MAX_INCOMING = 16
_MAX_FILE = 48 * 1024
_KEY_ID = re.compile(r"^[a-f0-9]{64}$")
_GITHUB_HOSTS = frozenset({
    "github.com", "www.github.com", "api.github.com",
    "raw.githubusercontent.com", "githubusercontent.com",
})


def public_peer_url(value: str) -> str:
    url = _seed_url(value)
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    if host in _GITHUB_HOSTS or host.endswith((".github.com", ".githubusercontent.com")):
        raise ValueError("GitHub используется только для bootstrap-каталога")
    return url


def valid_peer_rows(rows: object) -> tuple[dict[str, str], ...]:
    """Only accept bounded public HTTPS addresses, never private destinations."""
    if not isinstance(rows, list) or len(rows) > _MAX_INCOMING:
        raise ValueError("Некорректная или слишком большая адресная книга")
    accepted: dict[str, dict[str, str]] = {}
    for item in rows:
        if not isinstance(item, dict) or set(item) != {"key_id", "url"}:
            raise ValueError("Недопустимый формат адреса участника")
        key = item["key_id"]
        if not isinstance(key, str) or not _KEY_ID.fullmatch(key):
            raise ValueError("Некорректный идентификатор участника")
        url = public_peer_url(item["url"])
        accepted[key] = {"key_id": key, "url": url}
    return tuple(accepted.values())


class SwarmPeerCache:
    """Local bounded cache of untrusted candidate peer addresses with TTL."""

    def __init__(self, brain_dir: str | Path):
        self.path = Path(brain_dir) / "cluster" / "swarm-peers.json"
        self._lock = threading.RLock()
        self._peers: dict[str, tuple[str, int]] = {}

    def _prune(self, now: int) -> None:
        for key, (_, expires_at) in list(self._peers.items()):
            if expires_at <= now:
                self._peers.pop(key, None)

    def load(self) -> tuple[str, ...]:
        with self._lock:
            try:
                if not self.path.is_file() or self.path.stat().st_size > _MAX_FILE:
                    return ()
                doc = json.loads(self.path.read_text(encoding="utf-8"))
                if set(doc) != {"schema_version", "peers"} or doc["schema_version"] != 1:
                    return ()
                rows = doc["peers"]
                if not isinstance(rows, list) or len(rows) > _MAX_PEERS:
                    return ()
                now = int(time.time())
                for item in rows:
                    if not isinstance(item, dict) or set(item) != {"key_id", "url", "expires_at"}:
                        continue
                    expires = item["expires_at"]
                    if type(expires) is not int or not now < expires <= now + _PEER_TTL_SECONDS:
                        continue
                    try:
                        row = valid_peer_rows([{"key_id": item["key_id"], "url": item["url"]}])[0]
                    except (ValueError, TypeError):
                        continue
                    self._peers[row["key_id"]] = row["url"], expires
                self._prune(now)
            except (ValueError, TypeError, OSError, UnicodeError):
                return ()
            return self.urls()

    def learn(self, rows: object, *, own_key_id: str = "") -> tuple[str, ...]:
        parsed = valid_peer_rows(rows)
        now = int(time.time())
        with self._lock:
            self._prune(now)
            for item in parsed:
                if item["key_id"] == own_key_id:
                    continue
                self._peers[item["key_id"]] = item["url"], now + _PEER_TTL_SECONDS
            # Bounded, retaining the newest advertised endpoints.
            while len(self._peers) > _MAX_PEERS:
                oldest = min(self._peers, key=lambda k: self._peers[k][1])
                self._peers.pop(oldest)
            self._save()
            return self.urls()

    def _save(self) -> None:
        rows = [
            {"key_id": key, "url": url, "expires_at": expiry}
            for key, (url, expiry) in self._peers.items()
        ]
        doc = {"schema_version": 1, "peers": rows}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def urls(self) -> tuple[str, ...]:
        with self._lock:
            self._prune(int(time.time()))
            return tuple(dict.fromkeys(url for url, _ in self._peers.values()))


__all__ = ["SwarmPeerCache", "public_peer_url", "valid_peer_rows"]
