"""Owner-scoped link to an independently authenticated Apache Guacamole gateway.

Guacamole itself provides the interactive browser terminal and graphical RDP
desktop. Leshy does not receive its passwords, session tokens or video.
No proxy to guacd or unauthenticated native CDP/SSH transports is created.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from .superagent_remote_permissions import RemotePermissionPolicy, VerifiedActor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS superagent_owner_remote_gateway (
    owner_id TEXT PRIMARY KEY,
    gateway_url TEXT NOT NULL
)
"""
_DNS = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")


def validate_gateway_url(value: str) -> str:
    """Only HTTPS origins or the literal loopback HTTP development endpoint."""
    if not isinstance(value, str) or not 12 <= len(value) <= 512 or value.strip() != value:
        raise ValueError("Укажите адрес Guacamole (HTTPS или локальный HTTP)")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        port = url.port
    except ValueError:
        raise ValueError("Некорректный адрес шлюза") from None
    if (url.scheme not in {"https", "http"}
            or not host or url.username is not None or url.password is not None
            or url.query or url.fragment
            or url.path not in {"/", "/guacamole", "/guacamole/"}
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("Шлюз должен использовать допустимый HTTPS-адрес без токенов")
    if url.scheme == "http" and host not in {"127.0.0.1", "::1"}:
        raise ValueError("Незашифрованный Guacamole разрешён только на локальном компьютере")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if (url.scheme == "http" or not _DNS.fullmatch(host)
                or host.startswith(".") or ".." in host
                or host in {"localhost", "localhost.localdomain"}
                or host.endswith(".local") or host.endswith(".internal")
                or any(len(label) > 63 or label.startswith("-") or label.endswith("-")
                       for label in host.split("."))):
            raise ValueError("Некорректное имя сервера Guacamole") from None
    else:
        if url.scheme == "http" and str(ip) not in {"127.0.0.1", "::1"}:
            raise ValueError("Не разрешён удалённый HTTP")
        if ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError("Некорректный IP шлюза")
    # Normalize protocol and the known Guacamole context only. Never redirect
    # the browser to a stored username/password or arbitrary URL path.
    normalized_host = f"[{host}]" if ":" in host else host
    suffix = f":{port}" if port else ""
    path = "/guacamole/" if url.path.startswith("/guacamole") else "/"
    return f"{url.scheme}://{normalized_host}{suffix}{path}"


def get_remote_gateway(policy: RemotePermissionPolicy, actor: VerifiedActor) -> dict[str, Any]:
    policy._check_actor(actor)
    with policy.storage.connection() as conn:
        conn.execute(_SCHEMA)
        row = conn.execute(
            "SELECT gateway_url FROM superagent_owner_remote_gateway WHERE owner_id=?",
            (actor.owner_id,),
        ).fetchone()
    url = str(row["gateway_url"]) if row else ""
    return {
        "configured": bool(url),
        "url": url,
        "protocols": ["ssh", "rdp"],
        "authentication": "separate_guacamole_login",
        "ssh_pty_via_gateway": bool(url),
        "rdp_screen_via_gateway": bool(url),
        "ai_desktop_control": False,
    }


def set_remote_gateway(policy: RemotePermissionPolicy, actor: VerifiedActor,
                       url: str) -> dict[str, Any]:
    policy._check_actor(actor)
    normalized = validate_gateway_url(url) if url else ""
    with policy.storage.connection() as conn:
        conn.execute(_SCHEMA)
        if normalized:
            conn.execute(
                "INSERT INTO superagent_owner_remote_gateway(owner_id,gateway_url) "
                "VALUES(?,?) ON CONFLICT(owner_id) DO UPDATE SET "
                "gateway_url=excluded.gateway_url",
                (actor.owner_id, normalized),
            )
        else:
            conn.execute(
                "DELETE FROM superagent_owner_remote_gateway WHERE owner_id=?",
                (actor.owner_id,),
            )
    policy.storage.audit("superagent.remote_gateway.configured", {
        "owner_id_hash": __import__("hashlib").sha256(
            actor.owner_id.encode("utf-8")).hexdigest()[:16],
        "enabled": bool(normalized),
    })
    return get_remote_gateway(policy, actor)


__all__ = ["validate_gateway_url", "get_remote_gateway", "set_remote_gateway"]
