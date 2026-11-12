"""Conservative single-host TCP/UDP operating-system hints for SuperAgent.

Probes are limited, non-credentialed and read-only. A port cannot prove the
remote OS; authoritative classification requires authenticated host checks.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import socket
from typing import Any

_TCP_PORTS = (22, 135, 445, 3389, 5985, 5986, 111, 2049)
_UDP_PORTS = (53, 137)
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_REMOTE_CONTEXT = re.compile(r"(?i)\b(?:сервер\w*|узел|узла|хост\w*|host|server|ip|адрес\w*|машин\w*|компьютер\w*)\b")


def validate_target(host: str) -> str:
    """Accept only one routable/unicast literal, never domains or CIDRs."""
    try:
        addr = ipaddress.ip_address(str(host).strip())
    except ValueError as exc:
        raise ValueError("Для удалённой диагностики укажите один IP-адрес сервера") from exc
    if addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_link_local:
        raise ValueError("Этот IP-адрес не разрешён для удалённой диагностики")
    if addr.version == 4 and addr == ipaddress.IPv4Address("255.255.255.255"):
        raise ValueError("Широковещательный IP-адрес не разрешён")
    return str(addr)


def target_from_request(request: str) -> str | None:
    """Extract a literal IPv4 host only from an explicit user server request."""
    value = str(request or "")
    if not _REMOTE_CONTEXT.search(value):
        return None
    valid = []
    for match in _IPV4.finditer(value):
        try:
            valid.append(validate_target(match.group()))
        except ValueError:
            continue
    unique = list(dict.fromkeys(valid))
    return unique[0] if len(unique) == 1 else None


def _tcp(host: str, port: int, timeout: float) -> tuple[str, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if port == 22:
                try:
                    sock.settimeout(timeout)
                    banner = sock.recv(256).decode("ascii", errors="ignore")
                except (OSError, UnicodeError):
                    banner = ""
                low = banner.lower()
                if "ssh-" in low:
                    if "windows" in low or "win32" in low:
                        return "open", "ssh_windows"
                    if any(x in low for x in ("ubuntu", "debian", "raspbian", "alpine", "opensuse")):
                        return "open", "ssh_linux"
                    if "freebsd" in low or "openbsd" in low:
                        return "open", "ssh_unix"
            return "open", ""
    except ConnectionRefusedError:
        return "closed", ""
    except (TimeoutError, OSError):
        return "unknown", ""


def _udp_query(port: int, txn: bytes) -> bytes:
    if port == 53:
        # Standard DNS A query.
        name = b"\x07example\x07invalid\x00"
        return txn + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + name + b"\x00\x01\x00\x01"
    # NetBIOS wildcard node-status request. Windows and Samba may both answer.
    wildcard = bytes([0x2A]) + bytes(15)
    encoded = b"".join(bytes((0x41 + (byte >> 4), 0x41 + (byte & 0x0F))) for byte in wildcard)
    return txn + b"\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x20" + encoded + b"\x00\x00\x21\x00\x01"


def _udp(host: str, port: int, timeout: float) -> str:
    txn = secrets.token_bytes(2)
    data = _udp_query(port, txn)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(data, (host, port))
            answer, remote = sock.recvfrom(512)
            if str(remote[0]) == host and len(answer) >= 12 and answer[:2] == txn and answer[2] & 0x80:
                return "responsive"
    except (TimeoutError, OSError):
        pass
    # No UDP reply does NOT mean closed; many services do not answer probes.
    return "no_response"


def _classify(tcp: dict[int, dict[str, str]], udp: dict[int, str]) -> tuple[str, str, list[str]]:
    windows = 0
    linux = 0
    signals = []
    if tcp[22]["banner_hint"] == "ssh_windows":
        windows += 4
        signals.append("SSH-сервис сообщает о Windows")
    elif tcp[22]["banner_hint"] == "ssh_linux":
        linux += 4
        signals.append("SSH-сервис содержит признак Linux-дистрибутива")
    elif tcp[22]["banner_hint"] == "ssh_unix":
        signals.append("SSH-сервис содержит признак BSD/Unix, не Linux")
    if tcp[5985]["status"] == "open" or tcp[5986]["status"] == "open":
        windows += 2
        signals.append("Доступен типичный порт WinRM (возможна эмуляция)")
    if tcp[3389]["status"] == "open":
        windows += 1
        signals.append("Доступен RDP (работает и на Linux)")
    if tcp[135]["status"] == "open":
        windows += 1
        signals.append("Доступен RPC/TCP 135")
    if tcp[111]["status"] == "open" and tcp[2049]["status"] == "open":
        linux += 1
        signals.append("Доступны RPCbind/NFS, это не уникальный признак Linux")
    if tcp[445]["status"] == "open":
        signals.append("Доступен SMB (Windows или Samba)")
    if tcp[22]["status"] == "open":
        signals.append("Доступен SSH (Windows, Linux или BSD)")
    if udp[137] == "responsive":
        signals.append("UDP/137 отвечает NetBIOS (Windows или Samba)")
    if udp[53] == "responsive":
        signals.append("UDP/53 отвечает DNS (ОС по этому факту не определяется)")
    if windows >= 2 and windows >= linux + 2:
        return "Windows", "medium" if windows >= 4 else "low", signals
    if linux >= 4 and linux >= windows + 2:
        return "Linux", "medium", signals
    return "unknown", "unknown", signals


def probe_remote_os(host: str, *, timeout: float = 0.45) -> dict[str, Any]:
    """Perform at most eight TCP and two UDP probes against one explicit host."""
    target = validate_target(host)
    period = max(0.1, min(float(timeout), 1.0))
    tcp = {port: {"status": result[0], "banner_hint": result[1]}
           for port in _TCP_PORTS for result in (_tcp(target, port, period),)}
    udp = {port: _udp(target, port, period) for port in _UDP_PORTS}
    guessed, confidence, signals = _classify(tcp, udp)
    return {
        "host": target,
        "os_family": guessed,
        "confidence": confidence,
        "verified": False,
        "tcp": {str(port): value["status"] for port, value in tcp.items()},
        "udp": {str(port): value for port, value in udp.items()},
        "signals": signals,
        "warning": "TCP/UDP-признаки не доказывают операционную систему. Для обновления нужна проверка после авторизованного входа.",
    }


__all__ = ["probe_remote_os", "target_from_request", "validate_target"]
