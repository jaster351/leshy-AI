"""Evidence-first IP diagnostic: bounded TCP probes and explicit external check status."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import socket


def _tcp_probe(ip: str, port: int, timeout: float) -> dict:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return {"port": port, "status": "connected"}
    except ConnectionRefusedError:
        return {"port": port, "status": "refused"}
    except socket.timeout:
        return {"port": port, "status": "timeout"}
    except OSError as exc:
        return {"port": port, "status": "error", "error_type": type(exc).__name__}


def diagnose_ip(ip: str, *, timeout: float = 4.0) -> dict:
    """Perform only authorized, narrow tests. Never attribute blocking from TCP alone."""
    target = ipaddress.ip_address(ip)
    if not target.is_global:
        raise ValueError("Only globally routable public IP addresses are supported")
    timeout = max(0.5, min(float(timeout), 10.0))
    with ThreadPoolExecutor(max_workers=2) as pool:
        probes = list(pool.map(lambda port: _tcp_probe(str(target), port, timeout), (22, 3389)))
    return {
        "target": str(target),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "vantage_point": "local_runtime",
        "tcp": probes,
        "cheburcheck": {"status": "not_checked", "reason": "connector_not_configured"},
        "check_host": {"status": "not_checked", "reason": "connector_not_configured"},
        "verdict": "inconclusive",
        "tspu_blocking": "unconfirmed",
        "message": "TCP results alone cannot establish TSPU/RKN blocking. Independent Russian and foreign checks are required.",
    }
