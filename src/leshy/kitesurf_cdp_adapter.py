"""Optional, read-only CDP bridge to a *local* Kitesurf browser engine.

Kitesurf is an experimental CDP engine, not Chrome and not an anti-bot bypass.
This adapter contains original Leshy code; it does not bundle the upstream
project, deploy Workers or install any software. It is disabled unless the
administrator explicitly sets LESHY_KITESURF_CDP_URL.

A Kitesurf instance has no authentication of its own. Only literal loopback
endpoints are accepted, with no userinfo, path or remote API token overrides.
All page requests require a CDP interception rule installed before navigation;
if the engine does not implement the necessary CDP subset we fail closed and
allow the existing local Chromium adapter to take over.
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .browser_agent import BrowserAction, BrowserActionResult, BrowserRisk, _browser_ip_literal
from .diagnostics import event, problem


_SUPPORTED = frozenset({"research", "read", "open", "extract"})
_CHALLENGE = (
    "captcha", "капча", "verify you are human",
    "cloudflare challenge", "подтвердите, что вы человек",
)
_DEFAULT_TIMEOUT_MS = 15000


def local_kitesurf_endpoint(raw: str) -> tuple[str, str]:
    """Return (validated WS URL, HTTP discovery URL) or reject remote CDP."""
    if not isinstance(raw, str) or not raw or len(raw) > 256 or raw.strip() != raw:
        raise ValueError("Нужен локальный URL Kitesurf CDP")
    try:
        parsed = urlsplit(raw)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError:
        raise ValueError("Kitesurf должен быть доступен по буквальному 127.0.0.1 или ::1") from None
    if (parsed.scheme != "ws" or address not in {
                ipaddress.IPv4Address("127.0.0.1"), ipaddress.IPv6Address("::1"),
            }
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or parsed.path != "/devtools/browser"
            or port is None or not 1024 <= port <= 65535):
        raise ValueError("Разрешён только ws://127.0.0.1:PORT/devtools/browser")
    host = f"[{address.compressed}]" if address.version == 6 else str(address)
    return (
        f"ws://{host}:{port}/devtools/browser",
        f"http://{host}:{port}/json/version",
    )


def _target_ok(url: str, hosts: tuple[str, ...]) -> bool:
    """No internal IP, nonstandard port, URL credentials or non-HTTP target."""
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().strip(".")
        port = parsed.port
    except ValueError:
        return False
    if (parsed.scheme not in {"https", "http"} or not host
            or parsed.username is not None or parsed.password is not None
            or host == "localhost" or host.endswith(".localhost")
            or host.endswith(".local") or host.endswith(".internal")
            or port is not None and port != (443 if parsed.scheme == "https" else 80)):
        return False
    address = _browser_ip_literal(host)
    if address is not None and not address.is_global:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in hosts)


def _allowed_hosts(action: BrowserAction) -> tuple[str, ...]:
    raw = action.arguments.get("allowed_hosts") or ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or len(raw) > 20:
        return ()
    result = []
    for item in raw:
        if not isinstance(item, str):
            return ()
        host = item.strip().lower().strip(".")
        if not host or len(host) > 253 or "/" in host or ":" in host or "*" in host or "@" in host:
            return ()
        result.append(host)
    if not result:
        try:
            host = (urlsplit(action.url).hostname or "").lower().strip(".")
        except ValueError:
            return ()
        result = [host]
    return tuple(dict.fromkeys(result))


class KitesurfCdpAdapter:
    """Best-effort CDP page reading, never a general action/mutation transport."""

    adapter_id = "kitesurf-cdp"

    def __init__(self, endpoint: str | None = None, *, timeout_ms: int = _DEFAULT_TIMEOUT_MS):
        self.raw_endpoint = (endpoint if endpoint is not None else
                             os.environ.get("LESHY_KITESURF_CDP_URL", ""))
        self.timeout_ms = max(3000, min(int(timeout_ms), 30000))

    def available(self) -> bool:
        if not self.raw_endpoint:
            return False
        try:
            local_kitesurf_endpoint(self.raw_endpoint)
            import playwright.sync_api  # noqa: F401
            return True
        except (ValueError, ImportError):
            return False

    @staticmethod
    def _probe(discovery_url: str) -> bool:
        """An explicit local discovery endpoint; never consult system proxies."""
        opener = build_opener(ProxyHandler({}))
        try:
            response = opener.open(Request(
                discovery_url, headers={"Accept": "application/json"},
            ), timeout=2.0)
            with response:
                if response.status != 200:
                    return False
                raw = response.read(4097)
            if len(raw) > 4096:
                return False
            document = json.loads(raw.decode("utf-8"))
            return (
                isinstance(document, dict) and
                str(document.get("Browser") or "").startswith("Kitesurf/")
                and str(document.get("Protocol-Version") or "") == "1.3"
            )
        except (OSError, ValueError, UnicodeError, TimeoutError):
            return False

    def execute(self, action: BrowserAction) -> BrowserActionResult:
        if action.risk is not BrowserRisk.READ or action.name not in _SUPPORTED:
            return BrowserActionResult("unsupported", "Kitesurf предназначен только для чтения")
        if not self.available():
            return BrowserActionResult("unavailable", "Локальный Kitesurf не настроен")
        try:
            endpoint, discovery = local_kitesurf_endpoint(self.raw_endpoint)
        except ValueError:
            return BrowserActionResult("unavailable", "Небезопасный адрес CDP")
        hosts = _allowed_hosts(action)
        if not hosts or not _target_ok(action.url, hosts):
            return BrowserActionResult("blocked", "Адрес страницы запрещён политикой браузера")
        if not self._probe(discovery):
            return BrowserActionResult("unavailable", "Локальный движок Kitesurf недоступен")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return BrowserActionResult("unavailable", "Playwright не установлен")

        context = None
        browser = None
        started = time.monotonic()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(endpoint, timeout=min(self.timeout_ms, 7500))
                # New context isolates pages/cookies from all other owners.
                # If Kitesurf's partial CDP omits contexts or Fetch interception,
                # do not navigate; Chromium can handle the read instead.
                context = browser.new_context(
                    locale="ru-RU",
                    viewport={"width": 1366, "height": 768},
                    service_workers="block",
                )

                def authorize_request(route: Any) -> None:
                    if _target_ok(str(route.request.url), hosts):
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", authorize_request)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto(action.url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                if not _target_ok(page.url, hosts):
                    return BrowserActionResult("blocked", "Переход на чужой домен запрещён")
                text = page.locator("body").inner_text(timeout=min(self.timeout_ms, 8000))
                title = page.title()
                final_url = page.url
                if any(marker in text.lower() for marker in _CHALLENGE):
                    return BrowserActionResult(
                        "needs_user_action",
                        "Сайт требует участия владельца (CAPTCHA/challenge).",
                        {"url": final_url, "browser": self.adapter_id},
                    )
                compact = "\n".join(line.strip() for line in text.splitlines() if line.strip())
                if len(compact) < 40:
                    return BrowserActionResult(
                        "unavailable", "Kitesurf пока не смог извлечь достаточно текста",
                    )
                limit = max(1000, min(int(action.arguments.get("text_limit", 12000)), 20000))
                compact = compact[:limit]
                elapsed = int((time.monotonic() - started) * 1000)
                event("browser_kitesurf_read_completed", host=(urlsplit(final_url).hostname or ""),
                      chars=len(compact), elapsed_ms=elapsed)
                return BrowserActionResult(
                    "ok", "Страница прочитана локальным CDP-движком Kitesurf",
                    {
                        "url": final_url, "title": title[:500],
                        "visible_text": compact, "result": compact,
                        "browser": self.adapter_id, "elapsed_ms": elapsed,
                    },
                )
        except Exception as exc:
            # Do not publish user URL, cookies or raw page text to diagnostics.
            problem("browser_kitesurf_cdp_unavailable", type(exc).__name__)
            return BrowserActionResult(
                "unavailable",
                "Экспериментальный CDP-движок не поддержал действие; доступен обычный Chromium",
            )
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass


__all__ = ["KitesurfCdpAdapter", "local_kitesurf_endpoint"]
