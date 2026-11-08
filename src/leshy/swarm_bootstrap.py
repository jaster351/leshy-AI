from __future__ import annotations

"""Static GitHub entry directory for the Leshy public swarm.

GitHub is READ ONLY: no user IDs, online status, learned facts, or task
payloads are ever published there. The pinned repository contains only stable
entrypoints. After discovery, peers must use their own network protocols.

This module does not implement a global mesh; it supplies trusted HTTPS seed
URLs to the existing signed hello client and persists that directory locally.
"""

from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
from urllib import parse, request

GITHUB_MANIFEST_URL = (
    "https://raw.githubusercontent.com/jaster351/leshy-AI/"
    "main/swarm/bootstrap.json"
)
NETWORK_ID = "leshy-public-v1"
_MAX_BYTES = 24 * 1024
_MAX_SEEDS = 16


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _seed_url(raw: str, *, allow_local_http: bool = False) -> str:
    if not isinstance(raw, str) or not 1 <= len(raw) <= 320:
        raise ValueError("Некорректный адрес начального узла")
    try:
        parsed = parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Неверный URL или порт") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    if loopback and not allow_local_http:
        raise ValueError("Loopback допустим только в локальном пилоте")
    if not host or parsed.username is not None or parsed.password is not None:
        raise ValueError("Адрес seed не должен содержать учётных данных")
    if parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/v1/swarm"}:
        raise ValueError("Seed должен быть HTTPS origin или /v1/swarm")
    if parsed.scheme != "https" and not (
        allow_local_http and loopback and parsed.scheme == "http"
    ):
        raise ValueError("Seed требует HTTPS")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Неверный порт seed")
    if not loopback:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".local", ".lan", ".internal", ".localhost", ".test", ".invalid")
        ):
            raise ValueError("Seed должен быть публично доступным")
        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            literal_ip = None
        if literal_ip is not None and not literal_ip.is_global:
            raise ValueError("Локальные и частные адреса в публичном каталоге запрещены")
        if literal_ip is None and "." not in host:
            raise ValueError("Seed требует публичный DNS-адрес")
    return parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def validate_manifest(value: dict, *, allow_local_http: bool = False) -> tuple[str, ...]:
    """Fail closed; invalid manifests never replace a working local cache."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "network_id", "seeds",
    }:
        raise ValueError("Неизвестная схема GitHub-каталога")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Неподдерживаемый протокол каталога")
    if value["network_id"] != NETWORK_ID:
        raise ValueError("Каталог относится к другой сети")
    seeds = value["seeds"]
    if not isinstance(seeds, list) or len(seeds) > _MAX_SEEDS:
        raise ValueError("Недопустимый список начальных узлов")
    result = []
    for entry in seeds:
        if not isinstance(entry, dict) or set(entry) != {"id", "url"}:
            raise ValueError("Seed должен содержать только id и url")
        seed_id = entry["id"]
        if not isinstance(seed_id, str) or not 1 <= len(seed_id) <= 40 or not all(
            c.isascii() and (c.isalnum() or c in "-_") for c in seed_id
        ):
            raise ValueError("Недопустимое имя seed")
        url = _seed_url(entry["url"], allow_local_http=allow_local_http)
        if url not in result:
            result.append(url)
    return tuple(result)


class GitHubBootstrapDirectory:
    """Read-only public manifest, with local cache for offline restarts."""

    def __init__(self, brain_dir: str | Path, *, url: str = GITHUB_MANIFEST_URL,
                 allow_local_http: bool = False):
        if url != GITHUB_MANIFEST_URL and os.getenv("LESHY_SWARM_DEV_MANIFEST_URL", "") != "1":
            raise ValueError("Каталог разрешён только из канонического GitHub-репозитория")
        self.url = url
        self.cache_path = Path(brain_dir) / "cluster" / "bootstrap-directory.json"
        self.allow_local_http = allow_local_http
        self.last_error = ""
        self.last_digest = ""
        self.source = ""
        self._seeds: tuple[str, ...] = ()

    def cached(self) -> tuple[str, ...]:
        try:
            if not self.cache_path.is_file() or self.cache_path.stat().st_size > _MAX_BYTES:
                return ()
            raw = self.cache_path.read_bytes()
            doc = json.loads(raw)
            urls = validate_manifest(doc, allow_local_http=self.allow_local_http)
        except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return ()
        self._seeds = urls
        self.last_digest = sha256(raw).hexdigest()
        self.source = "local_cache"
        return urls

    def refresh(self) -> tuple[str, ...]:
        # HTTPS verification stays enabled. Do not follow redirects to arbitrary
        # origins, including when the repository moves or gets compromised.
        req = request.Request(
            self.url, headers={"Accept": "application/json",
                               "User-Agent": "LeshySwarmBootstrap/1"},
        )
        opener = request.build_opener(request.ProxyHandler(), _NoRedirect())
        try:
            with opener.open(req, timeout=6) as response:
                if response.status != 200:
                    raise ValueError("GitHub не вернул каталог")
                raw = response.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise ValueError("Слишком большой каталог")
            doc = json.loads(raw)
            urls = validate_manifest(doc, allow_local_http=self.allow_local_http)
            # Persist only the approved small public catalog, never node secrets.
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            try:
                tmp.chmod(0o600)
            except OSError:
                pass
            tmp.replace(self.cache_path)
            self._seeds = urls
            self.source = "github"
            self.last_digest = sha256(raw).hexdigest()
            self.last_error = ""
            return urls
        except Exception as exc:
            self.last_error = type(exc).__name__
            # Malformed/failed refresh must not discard an existing cache.
            if not self._seeds:
                self.cached()
            return self._seeds

    @property
    def seeds(self) -> tuple[str, ...]:
        return self._seeds


__all__ = [
    "GITHUB_MANIFEST_URL", "NETWORK_ID", "GitHubBootstrapDirectory",
    "validate_manifest",
]
