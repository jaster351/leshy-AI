"""Bounded TCP service fingerprinting for the network-blocking diagnostic.

Service banners are hints, not proof of the remote operating system.
Probe only common ports, avoid credentials, and do not attempt exploitation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ipaddress
import re
import socket
import ssl
import time


_SERVICE = {22: "SSH", 80: "HTTP", 443: "HTTPS", 445: "SMB", 3389: "RDP"}
_EXTRA_PORTS = (445,)
_READ_LIMIT = 2048


def _public_dns(target: str) -> bool:
    """Refuse diagnostics if DNS currently resolves to a non-public address."""
    try:
        addresses = socket.getaddrinfo(target, None, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(item[4][0]).is_global for item in addresses
        )
    except (OSError, ValueError):
        return False


def _safe_banner(value: bytes) -> str:
    text = value[:_READ_LIMIT].decode("latin-1", errors="replace")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return " ".join(text.split())[:160]


def _http_headers(value: bytes) -> tuple[str, str]:
    raw = value[:_READ_LIMIT].decode("latin-1", errors="replace")
    status = raw.splitlines()[0][:100] if raw else ""
    match = re.search(r"(?im)^server:\s*([^\r\n]{1,100})", raw)
    return status, match.group(1).strip() if match else ""


def _probe_port(target: str, port: int, timeout: float) -> dict:
    row = {"port": port, "service": _SERVICE[port], "status": "error",
           "detail": "", "latency_ms": None, "banner": ""}
    started = time.monotonic()
    try:
        with socket.create_connection((target, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            row["status"] = "connected"
            row["latency_ms"] = max(0, int((time.monotonic() - started) * 1000))
            if port in (80, 443):
                stream = sock
                if port == 443:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE  # fingerprint only; no trust assertions
                    try:
                        stream = context.wrap_socket(sock, server_hostname=target)
                        row["detail"] = "TLS " + str(stream.version() or "unknown")
                    except (ssl.SSLError, OSError) as exc:
                        row["detail"] = "TLS negotiation: " + type(exc).__name__
                        return row
                try:
                    stream.sendall(
                        ("HEAD / HTTP/1.0\r\nHost: " + target +
                         "\r\nConnection: close\r\n\r\n").encode("ascii")
                    )
                    status, server = _http_headers(stream.recv(_READ_LIMIT))
                    row["banner"] = server
                    if status.startswith("HTTP/"):
                        row["detail"] = (row["detail"] + " " + status).strip()
                except (OSError, ValueError):
                    pass
            return row
    except ConnectionRefusedError:
        row["status"] = "refused"
    except (socket.timeout, TimeoutError):
        row["status"] = "timeout"
    except OSError as exc:
        row["detail"] = type(exc).__name__
    return row


def _ssh_banner(target: str, timeout: float) -> str:
    try:
        with socket.create_connection((target, 22), timeout=timeout) as sock:
            sock.settimeout(timeout)
            value = sock.recv(256)
            banner = _safe_banner(value)
            return banner if banner.startswith("SSH-") else ""
    except OSError:
        return ""


def _classify(ports: list[dict]) -> tuple[str, str, list[str]]:
    hints: list[tuple[str, str]] = []
    for row in ports:
        text = str(row.get("banner") or "").lower()
        port = row["port"]
        if "openssh_for_windows" in text or "microsoft-iis" in text:
            hints.append(("Windows", f"TCP/{port}: {row['banner'][:90]}"))
        elif any(word in text for word in ("ubuntu", "debian", "freebsd", "openbsd", "alpine", "raspbian")):
            hints.append(("Linux/Unix", f"TCP/{port}: {row['banner'][:90]}"))
    families = {name for name, _ in hints}
    if len(families) > 1:
        return "неоднозначна", "низкая", [message for _, message in hints]
    if hints:
        return hints[0][0], "средняя", [message for _, message in hints]
    connected = {row["port"] for row in ports if row["status"] == "connected"}
    if 3389 in connected or 445 in connected:
        return "возможно Windows", "низкая", [
            "Открыт RDP/3389 или SMB/445; эти протоколы существуют и на Linux (xrdp/Samba)."
        ]
    if 22 in connected:
        return "возможно Linux/Unix", "низкая", [
            "Открыт SSH/22; OpenSSH также доступен на Windows."
        ]
    return "не определена", "отсутствует", [
        "Порты и сетевые ответы без характерных баннеров не позволяют определить ОС."
    ]


def collect_service_fingerprint(
    target: str, base_ports: list[dict], *, timeout: float = 2.0,
    include_web: bool = False,
) -> dict:
    """Run before external block checks; never return a certain OS identification."""
    timeout = max(0.5, min(float(timeout), 3.0))
    ports = [
        {"port": int(item["port"]), "service": _SERVICE.get(int(item["port"]), "TCP"),
         "status": str(item["status"]), "detail": "", "latency_ms": item.get("latency_ms"),
         "banner": ""}
        for item in base_ports
    ]
    if not _public_dns(target):
        return {"os_family": "не определена", "confidence": "отсутствует",
                "evidence": ["Цель не разрешается в публичный адрес; дополнительные пробы отменены."],
                "ports": ports, "completed": False}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="leshy-os-fingerprint") as pool:
        extra_ports = (*_EXTRA_PORTS, *((80, 443) if include_web else ()))
        futures = {p: pool.submit(_probe_port, target, p, timeout) for p in extra_ports}
        ssh = (pool.submit(_ssh_banner, target, timeout)
               if any(row["port"] == 22 and row["status"] == "connected" for row in ports)
               else None)
        ports.extend(futures[p].result() for p in extra_ports)
        if ssh is not None:
            for row in ports:
                if row["port"] == 22:
                    row["banner"] = ssh.result()
                    break
    ports.sort(key=lambda row: row["port"])
    family, confidence, evidence = _classify(ports)
    return {"os_family": family, "confidence": confidence,
            "evidence": evidence, "ports": ports, "completed": True}
