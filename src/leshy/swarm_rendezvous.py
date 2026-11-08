from __future__ import annotations

"""Local pilot rendezvous registry. Put TLS + rate limits at the public proxy.

This is a bootstrap/heartbeat registry, NOT an execution gateway or a public
production P2P mesh. Defaults to loopback only; it never accepts inference jobs.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import threading
import time
from pathlib import Path

from .community_identity import ReplayGuard, verify_envelope


_PEER_TTL = 240
_MAX_BODY = 4096


class RendezvousRegistry:
    def __init__(self, path: str | Path):
        self._path = str(path)
        self._lock = threading.RLock()
        self._replay_guard = ReplayGuard()
        with sqlite3.connect(self._path, timeout=10) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS peers ("
                "key_id TEXT PRIMARY KEY, last_seen INTEGER NOT NULL, "
                "protocol INTEGER NOT NULL)"
            )

    def register(self, envelope: dict) -> dict:
        info = verify_envelope(envelope, replay_guard=self._replay_guard)
        if info["kind"] != "cluster.hello":
            raise ValueError("Неизвестная операция")
        payload = info["payload"]
        if set(payload) != {
            "swarm_protocol", "version", "resource_sharing_enabled",
            "remote_execution_enabled",
        }:
            raise ValueError("Некорректные поля регистрации")
        if payload["swarm_protocol"] != 1 or (
            payload["resource_sharing_enabled"] is not False
            or payload["remote_execution_enabled"] is not False
        ):
            raise ValueError("На этом этапе разрешена только регистрация")
        version = payload["version"]
        if not isinstance(version, str) or not 1 <= len(version) <= 40:
            raise ValueError("Некорректная версия")
        now = int(time.time())
        with self._lock, sqlite3.connect(self._path, timeout=10) as db:
            db.execute("DELETE FROM peers WHERE last_seen<?", (now - _PEER_TTL,))
            db.execute(
                "INSERT INTO peers(key_id,last_seen,protocol) VALUES(?,?,1) "
                "ON CONFLICT(key_id) DO UPDATE SET last_seen=excluded.last_seen",
                (info["key_id"], now),
            )
            count = int(db.execute("SELECT COUNT(*) FROM peers").fetchone()[0])
        return {"accepted": True, "peer_count": count, "ttl_seconds": _PEER_TTL}

    def count(self) -> int:
        now = int(time.time())
        with self._lock, sqlite3.connect(self._path, timeout=10) as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM peers WHERE last_seen>=?", (now - _PEER_TTL,),
            ).fetchone()[0])


def make_handler(registry: RendezvousRegistry):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LeshySwarmRendezvous/0.1"

        def _respond(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                return self._respond(200, {"ready": True, "peers": registry.count()})
            return self._respond(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/v1/swarm/hello":
                return self._respond(404, {"error": "not_found"})
            try:
                declared = int(self.headers.get("Content-Length", "0"))
                if not 0 < declared <= _MAX_BODY:
                    return self._respond(413, {"error": "invalid_body_size"})
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                    return self._respond(415, {"error": "invalid_content_type"})
                envelope = json.loads(self.rfile.read(declared))
                return self._respond(200, registry.register(envelope))
            except (ValueError, TypeError, json.JSONDecodeError):
                return self._respond(400, {"error": "invalid_registration"})
            except Exception:
                return self._respond(503, {"error": "temporarily_unavailable"})

        def log_message(self, _format: str, *args) -> None:
            # Don't log peer IPs or user-submitted envelopes by default.
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный тестовый bootstrap Лешего")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=38865)
    parser.add_argument("--database", default="swarm-rendezvous.sqlite3")
    args = parser.parse_args()
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        parser.error(
            "Пилотный registry слушает только loopback. "
            "Для интернета используйте проверенный HTTPS reverse proxy, TLS, "
            "лимиты запросов и отдельную авторизацию оператора."
        )
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(RendezvousRegistry(args.database)))
    print(f"Leshy swarm bootstrap pilot http://{args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
