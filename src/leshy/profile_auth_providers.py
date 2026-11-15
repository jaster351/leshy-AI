"""Capabilities of external profile providers for a self-hosted, IP-only Leshy.

This is deliberately a read-only configuration report, NOT OAuth login.
Unverified provider claims must never grant access to an owner profile.
"""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit


def is_public_https_domain(url: str) -> bool:
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").strip().lower().rstrip(".")
        if parts.scheme != "https" or not host or not parts.netloc or parts.username or parts.password:
            return False
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return "." in host and not host.endswith((".localhost", ".internal", ".test", ".invalid"))
        return False
    except ValueError:
        return False


def provider_status() -> list[dict[str, str | bool]]:
    """No secrets, callback tokens, IP addresses, or guessed base URL are exposed."""
    public_url = os.getenv("LESHY_AUTH_PUBLIC_BASE_URL", "").strip()
    has_https_domain = is_public_https_domain(public_url)
    requirements = {
        "google": ("Google", "LESHY_GOOGLE_CLIENT_ID",
                   "Для Web OAuth нужен разрешённый HTTPS-домен; LAN-IP не подходит. "
                   "На том же ПК Google допускает localhost, но это не авторизация с другого устройства."),
        "apple": ("Apple", "LESHY_APPLE_CLIENT_ID",
                  "Apple Sign in для Web требует зарегистрированный HTTPS-домен и Services ID; IP/localhost запрещены."),
        "vk": ("VK ID", "LESHY_VK_CLIENT_ID",
               "Для Web OAuth нужен доверенный Redirect URI в кабинете VK ID. "
               "LAN-IP без подтверждённой конфигурации не объявляется поддерживаемым."),
    }
    items = []
    for key, (title, env_key, explanation) in requirements.items():
        configured = bool(os.getenv(env_key, "").strip())
        items.append({
            "id": key,
            "title": title,
            "type": "oauth",
            "enabled": False,
            "configured": configured,
            "requires_public_https_domain": True,
            "public_https_domain_configured": has_https_domain,
            "reason": explanation if not has_https_domain else
                      "Вход ещё не активирован: необходимы обработчики OAuth, проверка подписей, state и PKCE.",
        })
    items.append({
        "id": "gravatar", "title": "Gravatar", "type": "avatar",
        "enabled": True, "configured": True,
        "requires_public_https_domain": False,
        "public_https_domain_configured": has_https_domain,
        "reason": "Аватар по SHA-256 email. Не подтверждает владение email и не является способом входа.",
    })
    return items


__all__ = ["is_public_https_domain", "provider_status"]
