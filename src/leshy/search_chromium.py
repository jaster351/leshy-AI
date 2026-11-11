from __future__ import annotations

"""Read-only Chromium search with a *dedicated*, persistent Leshy profile.

An actual browser handles JS, Chromium's native User-Agent, normal cookies
and consent pages. Do not copy user Chrome/Edge profiles, spoof fingerprints,
solve CAPTCHA, ignore a challenge, or send credentials to search providers.

Search is deliberately restricted to known public search-engine URLs. This
class never browses the result URLs or executes user-supplied browser scripts.
"""

import os
from pathlib import Path
import re
from threading import Lock, RLock
import time
from urllib.parse import parse_qs, quote_plus, urlsplit, urlunsplit

from .browser_launch_policy import chromium_launch_policy
from .search_challenge_handoff import BROKER
from .research import SearchResult, SearchUnavailable
from .system_browser import ensure_system_browser

_ENGINE_URLS = {
    "bing": "https://www.bing.com/search?q={query}",
    "google": "https://www.google.com/search?q={query}",
    "duckduckgo": "https://duckduckgo.com/?q={query}&ia=web",
}
_ENGINE_SELECTORS = {
    "bing": "#b_results li.b_algo h2 a",
    "google": "#search a:has(h3)",
    "duckduckgo": 'a[data-testid="result-title-a"], article h2 a',
}
_CHALLENGE_MARKERS = (
    "verify you are human", "verify you're human",
    "unusual traffic", "please verify", "security challenge",
    "проверка безопасности", "подтвердите, что вы человек",
    "подтвердите, что вы не робот", "robot check", "are you a robot",
    "checking your browser", "just a moment...",
)
_BROWSER_LOCK = Lock()
_STATUS_LOCK = RLock()
_STATUS = {
    "state": "not_used",
    "engine": "",
    "browser": "",
    "last_success_at": "",
    "last_challenge_at": "",
    "last_error": "",
    "user_agent": "",
    "cookie_count": 0,
    "searches": 0,
    "successes": 0,
    "challenges": 0,
}
_LINK_LIMIT = 18


class SearchChallenge(SearchUnavailable):
    """This search engine requests a real person's browser interaction."""


def _search_profile(root: str | Path | None = None) -> Path:
    """Never touch a user's default Chrome/Edge profile."""
    brain = Path(root or os.getenv("LESHY_BRAIN_DIR") or "LeshyBrain").expanduser()
    return brain / "browser" / "search-chromium-profile"


def _engine(value: str | None = None) -> str:
    name = str(value or os.getenv("LESHY_CHROMIUM_SEARCH_ENGINE", "bing")).strip().lower()
    if name not in _ENGINE_URLS:
        raise ValueError("Недопустимый поисковик: разрешены bing, google, duckduckgo")
    return name


def _search_page(engine: str, query: str) -> str:
    return _ENGINE_URLS[engine].format(query=quote_plus(query[:400]))


def _is_challenge(title: str, body: str, status: int | None) -> bool:
    heading = str(title or "").lower()
    prefix = str(body or "")[:1500].lower()
    text = " ".join((heading + " " + prefix).split())
    if status in (403, 429):
        return True
    if any(marker in text for marker in _CHALLENGE_MARKERS):
        return True
    # The search topic itself may be "CAPTCHA"; matching this word in
    # arbitrary search results must not be mistaken for a challenge page.
    return "captcha" in heading or (
        "captcha" in prefix[:500] and any(
            marker in prefix[:500] for marker in ("robot", "verify", "проверк", "введите")
        )
    )


def _clean_result(url: str, title: str, snippet: str) -> SearchResult | None:
    """Reject search-page links and private/unsafe destinations."""
    from ipaddress import ip_address
    parsed = urlsplit(str(url or "").strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not hostname:
        return None
    if parsed.username or parsed.password or parsed.fragment:
        return None
    if hostname in {"localhost"} or hostname.endswith(
        (".localhost", ".local", ".lan", ".internal")
    ):
        return None
    try:
        ip = ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        return None
    # Some SERPs expose links to their own tracking/advertising services.
    if hostname in {"google.com", "www.google.com", "bing.com", "www.bing.com"}:
        if parsed.path in {"/search", "/", "/url", "/aclick"} or parsed.path.startswith("/ck/"):
            return None
    title = " ".join(str(title or "").split())[:450]
    snippet = " ".join(str(snippet or "").split())[:1300]
    if not title:
        return None
    return SearchResult(title, urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")), snippet)


def _collect_links(page, engine: str, limit: int) -> list[SearchResult]:
    """Read rendered result anchors, not raw HTTP pages or browser cookies."""
    selector = _ENGINE_SELECTORS[engine]
    links = page.locator(selector)
    out: list[SearchResult] = []
    seen: set[str] = set()
    for n in range(min(links.count(), _LINK_LIMIT)):
        link = links.nth(n)
        try:
            href = str(link.get_attribute("href", timeout=800) or "")
            title = str(link.inner_text(timeout=800) or "")
            # The result heading's parent is a limited public preview.
            snippet = str(link.locator("xpath=../..").inner_text(timeout=800) or "")
        except Exception:
            continue
        row = _clean_result(href, title, snippet)
        if row is None or row.url in seen:
            continue
        seen.add(row.url)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _status(**updates) -> None:
    with _STATUS_LOCK:
        _STATUS.update(updates)


def chromium_search_status() -> dict:
    with _STATUS_LOCK:
        return {**_STATUS, "profile": "dedicated_local_anonymous",
                "profile_persistent": True,
                "uses_personal_browser_profile": False,
                "captcha_handling": "live_owner_handoff_no_bypass"}


def _open_context(pw, *, root: Path, headless: bool):
    """Launch a real browser with persistent cookie jar and native UA."""
    browser = ensure_system_browser(auto_install=False)
    policy = chromium_launch_policy()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(root),
        executable_path=browser.executable,
        headless=headless if policy.can_open_window else True,
        chromium_sandbox=policy.chromium_sandbox,
        args=["--no-first-run", "--no-default-browser-check", *policy.args],
        accept_downloads=False,
        locale="ru-RU",
        viewport={"width": 1280, "height": 800},
        timeout=8_000,
    )
    return context, browser.name


class ChromiumSearch:
    """Use isolated Chrome/Edge browser for public search, no user browser data."""

    def __init__(self, *, engine: str | None = None, brain_dir: str | Path | None = None,
                 timeout_ms: int = 7_000):
        self.engine = _engine(engine)
        self.profile_dir = _search_profile(brain_dir)
        self.timeout_ms = max(2_000, min(7_000, int(timeout_ms)))

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        q = " ".join(str(query or "").split()).strip()
        if not q:
            return []
        with _STATUS_LOCK:
            _STATUS["searches"] += 1
        # Chromium locks the persistent profile. Avoid concurrent launches
        # with the same profile, and let another search backend answer instead.
        if not _BROWSER_LOCK.acquire(blocking=False):
            raise SearchUnavailable("Отдельный браузер Лешего уже выполняет поиск")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                context, name = _open_context(pw, root=self.profile_dir, headless=True)
                try:
                    _status(state="searching", engine=self.engine, browser=name)
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    response = page.goto(
                        _search_page(self.engine, q),
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                    status = response.status if response is not None else None
                    body = page.locator("body").inner_text(timeout=2_000)
                    title = page.title()
                    try:
                        user_agent = str(page.evaluate("navigator.userAgent") or "")[:240]
                        cookie_count = len(context.cookies())
                        _status(user_agent=user_agent, cookie_count=cookie_count)
                    except Exception:
                        pass
                    ticket = None
                    if _is_challenge(title, body, status):
                        # Give ordinary JavaScript/interstitial checks a chance
                        # to finish in the actual browser before asking a human.
                        page.wait_for_timeout(1400)
                        title = page.title()
                        body = page.locator("body").inner_text(timeout=2_000)
                        if _is_challenge(title, body, None) or (
                            status in (403, 429) and not _collect_links(page, self.engine, 1)
                        ):
                            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            _status(state="needs_human_verification", last_challenge_at=stamp)
                            with _STATUS_LOCK:
                                _STATUS["challenges"] += 1
                            ticket = BROKER.create(page, self.engine)
                            if ticket is None:
                                raise SearchChallenge("Другой поиск уже ожидает подтверждения")

                            def challenge_resolved() -> bool:
                                try:
                                    current_title = page.title()
                                    current_body = page.locator("body").inner_text(timeout=2_000)
                                    if _is_challenge(current_title, current_body, None):
                                        return False
                                    # A blank page or intermediate redirect is
                                    # NOT proof that a human challenge passed.
                                    return bool(_collect_links(page, self.engine, 1))
                                except Exception:
                                    return False

                            if not BROKER.await_solution(page, ticket, challenge_resolved):
                                raise SearchChallenge("Ожидание подтверждения CAPTCHA не завершилось")
                            _status(state="resumed_after_human_verification")
                    results = _collect_links(page, self.engine, max(1, min(int(limit), 10)))
                    if ticket is not None:
                        BROKER.complete(ticket, results)
                    if not results:
                        _status(state="no_results")
                        return []
                    _status(state="ok",
                            last_success_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            last_error="")
                    with _STATUS_LOCK:
                        _STATUS["successes"] += 1
                    return results
                finally:
                    context.close()  # Chromium persists cookies in its own profile
        except SearchChallenge:
            raise
        except Exception as exc:
            _status(state="error", last_error=type(exc).__name__)
            raise SearchUnavailable(
                "Изолированный Chromium-поиск недоступен: " + type(exc).__name__
            ) from None
        finally:
            _BROWSER_LOCK.release()


def interactive_verify(*, engine: str = "bing", brain_dir: str | Path | None = None) -> None:
    """Owner manually handles a site's challenge in Leshy's dedicated browser.

    No automation, third-party CAPTCHA solvers, credential copying, or browser
    fingerprint spoofing. Not supported on a server without a display.
    """
    from playwright.sync_api import sync_playwright
    policy = chromium_launch_policy()
    if not policy.can_open_window:
        raise RuntimeError("Для ручного подтверждения нужна графическая сессия")
    if not _BROWSER_LOCK.acquire(blocking=False):
        raise RuntimeError("Браузерный профиль занят текущим поиском; повторите позже")
    try:
        with sync_playwright() as pw:
            context, _ = _open_context(
                pw, root=_search_profile(brain_dir), headless=False,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                name = _engine(engine)
                page.goto(_ENGINE_URLS[name].split("?", 1)[0], wait_until="domcontentloaded")
                print(
                    "Открыт отдельный браузер Лешего. Выполните тестовый поиск. "
                    "Если сайт показывает CAPTCHA, решите её самостоятельно в этом окне. "
                    "Не входите в личные аккаунты: "
                    "этот профиль предназначен только для анонимного поиска.",
                    flush=True,
                )
                input("После проверки нажмите Enter в консоли для сохранения сессии... ")
            finally:
                context.close()
    finally:
        _BROWSER_LOCK.release()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Проверка CAPTCHA в отдельном профиле браузера Лешего")
    parser.add_argument("--verify", choices=tuple(_ENGINE_URLS), required=True)
    parser.add_argument("--brain-dir", default=None,
                        help="Корень LeshyBrain действующей установки, если нужен явный путь")
    args = parser.parse_args()
    interactive_verify(engine=args.verify, brain_dir=args.brain_dir)


if __name__ == "__main__":
    main()


__all__ = [
    "ChromiumSearch", "SearchChallenge", "chromium_search_status",
    "interactive_verify", "_clean_result", "_is_challenge",
]
