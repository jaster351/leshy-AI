from __future__ import annotations

"""Automatic *membership* in a configured public Leshy swarm.

Membership is deliberately separate from granting strangers CPU/GPU time,
sending personal data, executing cluster jobs, or accepting inbound connections.
No public service address is assumed: without a configured HTTPS bootstrap this
module reports "waiting_for_bootstrap" and never pretends to be online.
"""

import json
import os
import threading
import time
from urllib import parse, request

from .community_identity import CommunityIdentity
from .swarm_bootstrap import GitHubBootstrapDirectory
from .diagnostics import event, problem
from .storage import utcnow
from .version import VERSION


_SETTING = "swarm.public.auto_join"
_DEFAULT_ENABLED = True
_HEARTBEAT_SECONDS = 90
_DIRECTORY_REFRESH_SECONDS = 15 * 60
_MAX_RESPONSE_BYTES = 4096


class _RejectRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Swarm bootstrap redirect prohibited")


def bootstrap_endpoint(value: str, *, allow_local_http: bool = False) -> str:
    """Accept a configured HTTPS origin, never unknown/private redirect targets."""
    url = parse.urlsplit(str(value or "").strip())
    if not url.scheme and not url.netloc:
        return ""
    local = url.hostname in {"127.0.0.1", "::1", "localhost"}
    if (
        not url.hostname
        or url.username is not None or url.password is not None
        or url.query or url.fragment
        or (url.scheme != "https" and not (
            allow_local_http and local and url.scheme == "http"
        ))
    ):
        raise ValueError("Для публичного bootstrap требуется HTTPS URL без логина, query и fragment")
    try:
        port = url.port
    except ValueError as exc:
        raise ValueError("Некорректный порт bootstrap") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("Некорректный порт bootstrap")
    path = url.path.rstrip("/")
    if path and path != "/v1/swarm":
        raise ValueError("Bootstrap должен указывать на HTTPS origin или /v1/swarm")
    return parse.urlunsplit((url.scheme, url.netloc, "/v1/swarm/hello", "", ""))


class SwarmMembership:
    """Opt-out auto-discovery, without remote execution permissions.

    A new install has network membership enabled, as advertised in installer
    disclosure. No registration can take place until a trusted bootstrap URL
    is explicitly provided by the distributor. This is *not* a compute opt-in.
    """

    def __init__(self, app):
        self.app = app
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._identity: CommunityIdentity | None = None
        self._last_contact = ""
        self._last_error = ""
        self._peers_seen = 0
        self._status = "starting"
        self._bootstrap_error = ""
        self._allow_local_http = os.getenv("LESHY_SWARM_DEV_ALLOW_HTTP", "") == "1"
        self._directory = GitHubBootstrapDirectory(
            self.app.settings.brain_dir, allow_local_http=self._allow_local_http,
        )
        # Keep the previous environment override for private pilots, while
        # making the public GitHub catalog the default discovery route.
        self._manual_endpoint = ""
        try:
            self._manual_endpoint = bootstrap_endpoint(
                os.environ.get("LESHY_SWARM_BOOTSTRAP_URL", ""),
                allow_local_http=self._allow_local_http,
            )
        except ValueError as exc:
            self._bootstrap_error = str(exc)
        self._seed_urls = tuple(
            bootstrap_endpoint(url, allow_local_http=self._allow_local_http)
            for url in self._directory.cached()
        )
        self._endpoint = self._manual_endpoint or (self._seed_urls[0] if self._seed_urls else "")
        self._last_directory_refresh = 0.0
        self._seed_index = 0

    def enabled(self) -> bool:
        with self.app.storage.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (_SETTING,)).fetchone()
        if row is None:
            return _DEFAULT_ENABLED
        return str(row["value"]).lower() in {"1", "true", "on", "yes"}

    def set_enabled(self, enabled: bool) -> bool:
        with self.app.storage.connection() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_SETTING, "1" if enabled else "0"),
            )
        self.app.storage.audit("swarm.auto_join.changed", {
            "enabled": bool(enabled), "updated_at": utcnow(),
        })
        if not enabled:
            with self._lock:
                self._status = "disabled"
                self._peers_seen = 0
        self._wake.set()
        event("swarm_auto_join_changed", enabled=bool(enabled))
        return bool(enabled)

    def status(self) -> dict:
        enabled = self.enabled()
        with self._lock:
            return {
                "auto_join_enabled": enabled,
                "bootstrap_configured": bool(self._endpoint),
                "bootstrap_source": "environment" if self._manual_endpoint else self._directory.source or "github_catalog",
                "seed_count": len(self._seed_urls),
                "directory_digest": self._directory.last_digest,
                "github_directory_url": self._directory.url,
                "github_used_for_membership_only": True,
                "state": self._status if enabled else "disabled",
                "last_contact": self._last_contact,
                "last_error": self._last_error or self._bootstrap_error,
                "peer_count_reported": self._peers_seen if enabled else 0,
                "resource_sharing_enabled": False,
                "remote_execution_enabled": False,
                "global_mesh_connected": False,
                "note": (
                    "Реальная публичная сеть требует развёрнутого bootstrap и "
                    "защищённого транспорта исполнения; пока только регистрация"
                ),
            }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._run, name="leshy-swarm-membership", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def _discover_seeds(self) -> None:
        """Fetch GitHub only when no known seed works, at a bounded cadence.

        Once a seed is available, heartbeats go straight to that seed; GitHub
        receives no identity, task, knowledge or per-node status updates.
        """
        if self._manual_endpoint:
            return
        now = time.monotonic()
        if self._last_directory_refresh and now - self._last_directory_refresh < _DIRECTORY_REFRESH_SECONDS:
            return
        self._last_directory_refresh = now
        new_seeds = self._directory.refresh()
        if new_seeds:
            self._seed_urls = tuple(
                bootstrap_endpoint(url, allow_local_http=self._allow_local_http)
                for url in new_seeds
            )
            self._seed_index = 0
            self._endpoint = self._seed_urls[0]
            event("swarm_directory_loaded", seed_count=len(self._seed_urls),
                  source=self._directory.source)
        elif not self._seed_urls:
            self._endpoint = ""

    def _try_next_seed(self) -> bool:
        if self._manual_endpoint or len(self._seed_urls) <= 1:
            return False
        self._seed_index = (self._seed_index + 1) % len(self._seed_urls)
        self._endpoint = self._seed_urls[self._seed_index]
        return True

    def _hello(self) -> int:
        if not self._endpoint:
            raise RuntimeError("Отсутствует bootstrap")
        if self._identity is None:
            # Dedicated node identity, not any user's profile or Telegram identity.
            self._identity = CommunityIdentity(self.app.settings.brain_dir, "swarm:node")
        signed = self._identity.sign("cluster.hello", {
            "swarm_protocol": 1,
            "version": VERSION,
            "resource_sharing_enabled": False,
            "remote_execution_enabled": False,
        })
        payload = json.dumps(signed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = request.Request(
            self._endpoint,
            data=payload, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        local_dev = parse.urlsplit(self._endpoint).hostname in {"127.0.0.1", "::1", "localhost"}
        proxy = request.ProxyHandler({}) if local_dev else request.ProxyHandler()
        opener = request.build_opener(proxy, _RejectRedirect())
        with opener.open(req, timeout=5.0) as response:
            if response.status != 200:
                raise RuntimeError("Bootstrap отклонил регистрацию")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("Слишком большой bootstrap-ответ")
        data = json.loads(raw)
        if data.get("accepted") is not True:
            raise RuntimeError("Bootstrap не подтвердил регистрацию")
        count = data.get("peer_count", 0)
        if type(count) is not int or count < 0:
            raise ValueError("Некорректное число участников")
        return min(count, 1_000_000_000)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            delay = 30.0
            if not self.enabled():
                with self._lock:
                    self._status = "disabled"
            else:
                if not self._endpoint:
                    self._discover_seeds()
                if not self._endpoint:
                    with self._lock:
                        self._status = "waiting_for_seeds"
                        self._last_error = self._directory.last_error or self._bootstrap_error
                    self._wake.wait(delay)
                    self._wake.clear()
                    continue
                try:
                    count = self._hello()
                    with self._lock:
                        self._status = "registered"
                        self._last_error = ""
                        self._last_contact = utcnow()
                        self._peers_seen = count
                    failures = 0
                    delay = _HEARTBEAT_SECONDS
                    event("swarm_membership_registered", peer_count=count)
                except Exception as exc:
                    failures += 1
                    with self._lock:
                        self._status = "retrying"
                        self._last_error = type(exc).__name__
                    # Fail over to the next known seed without hitting GitHub.
                    # Refresh the public directory only after the known list
                    # has failed and the refresh interval permits it.
                    if not self._try_next_seed():
                        self._discover_seeds()
                    delay = min(300.0, 10.0 * 2 ** min(failures, 4))
                    problem("swarm_membership_failed", type(exc).__name__)
            self._wake.wait(delay)
            self._wake.clear()


__all__ = ["SwarmMembership", "bootstrap_endpoint"]
