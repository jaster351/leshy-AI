#!/usr/bin/env python3
"""Operator-owned European SSH reachability probe (TCP/22 only).

Run a standalone HTTP service on any IPv4 interface. Authenticate requests
and responses with an HMAC derived from an operator-generated numeric password.
The password itself is never transmitted. No SSH login or port scanning.
HTTP does not encrypt target IP addresses or other diagnostic metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import threading
import time


_LIMIT = threading.Lock()
_SEEN: list[float] = []
_ACTIVE = threading.BoundedSemaphore(value=2)
_MAX_PER_MINUTE = 15


def _check_tcp22(target: str) -> dict:
    stamp = time.monotonic()
    try:
        with socket.create_connection((target, 22), timeout=2.5):
            status = "connected"
    except ConnectionRefusedError:
        status = "refused"
    except (TimeoutError, socket.timeout):
        status = "timeout"
    except OSError:
        status = "error"
    return {
        "status": status,
        "elapsed_ms": max(0, int((time.monotonic() - stamp) * 1000)),
    }


def _validate_target(raw: object) -> str:
    if not isinstance(raw, str):
        raise ValueError("Expected IP address")
    value = ipaddress.ip_address(raw.strip())
    if not value.is_global or getattr(value, "ipv4_mapped", None):
        raise ValueError("Only public globally routed IP addresses")
    return str(value)


def _allowed_request() -> bool:
    now = time.monotonic()
    with _LIMIT:
        _SEEN[:] = [x for x in _SEEN if now - x < 60]
        if len(_SEEN) >= _MAX_PER_MINUTE:
            return False
        _SEEN.append(now)
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = "LeshyEUProbe/1"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        # Never log bearer tokens or target addresses.
        return

    def _respond(self, status: int, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, private")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self, body: dict, target: str, nonce: str) -> bool:
        timestamp = body.get("timestamp")
        proof = body.get("password")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            return False
        if abs(time.time() - timestamp) > 45:
            return False
        if not isinstance(proof, str) or re.fullmatch(r"[0-9a-f]{64}", proof) is None:
            return False
        message = f"v1|{target}|{nonce}|{timestamp}|22".encode("ascii")
        expected = hmac.new(
            self.server.password.encode("ascii"), message, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(proof, expected)

    def do_GET(self) -> None:
        self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/v1/ssh/check":
            self._respond(404, {"error": "not_found"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._respond(415, {"error": "json_required"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size < 2 or size > 512:
            self._respond(413, {"error": "invalid_body_size"})
            return
        try:
            body = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object")
            target = _validate_target(body["target"])
            nonce = body["nonce"]
            if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
                raise ValueError("invalid nonce")
        except (KeyError, TypeError, ValueError, UnicodeError):
            self._respond(400, {"error": "invalid_target_or_nonce"})
            return
        if not self._authorized(body, target, nonce):
            self._respond(401, {"error": "unauthorized"})
            return
        if not _allowed_request():
            self._respond(429, {"error": "rate_limited"})
            return
        if not _ACTIVE.acquire(blocking=False):
            self._respond(429, {"error": "busy"})
            return
        try:
            result = {
                "version": 1,
                "target": target, "port": 22, "nonce": nonce,
                "country_code": self.server.country_code,
                "node": self.server.node,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "completed": True,
                "attempts": [_check_tcp22(target) for _ in range(3)],
            }
            content = json.dumps(
                result, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            result["signature"] = hmac.new(
                self.server.password.encode("ascii"), content, hashlib.sha256
            ).hexdigest()
            self._respond(200, result)
        finally:
            _ACTIVE.release()


def main() -> None:
    password = os.environ.get("LESHY_PROBE_PASSWORD", "").strip()
    country = os.environ.get("LESHY_PROBE_COUNTRY_CODE", "").strip().upper()
    node = os.environ.get("LESHY_PROBE_NODE", "europe-1").strip()
    if re.fullmatch(r"[0-9]{48,128}", password) is None:
        raise SystemExit("LESHY_PROBE_PASSWORD must contain 48-128 random digits")
    if not re.fullmatch(r"[A-Z]{2}", country) or country == "RU":
        raise SystemExit("Set LESHY_PROBE_COUNTRY_CODE to the actual foreign country")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", node):
        raise SystemExit("Invalid LESHY_PROBE_NODE")
    host = os.environ.get("LESHY_PROBE_BIND_HOST", "0.0.0.0")
    if host not in ("127.0.0.1", "0.0.0.0"):
        raise SystemExit("Bind host must be loopback or all IPv4 interfaces")
    port = int(os.environ.get("LESHY_PROBE_PORT", "8768"))
    server = ThreadingHTTPServer((host, port), Handler)
    server.password = password
    server.country_code = country
    server.node = node
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
