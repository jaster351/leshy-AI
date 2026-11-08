from __future__ import annotations

"""Independent, bounded search backends for an interactive Leshy request.

When DuckDuckGo/Yahoo/Startpage time out, no single provider should consume
the entire interactive search budget. The caller gets the first real source,
while other attempts finish on their own within their per-provider timeouts.

Never claim internet success solely because an HTTP endpoint responded 200.
"""

from dataclasses import dataclass
import html
import queue
import re
import threading
import time
from urllib import parse, request
from xml.etree import ElementTree

from .diagnostics import event
from .research import FallbackSearch, SearchResult, SearchUnavailable


_RACE_DEADLINE_SECONDS = 8.0
# Global concurrency cap: protects interactive chat against background
# self-learning storms and detached network workers.
_MAX_PARALLEL_SEARCH_WORKERS = 12
_SLOTS = threading.BoundedSemaphore(_MAX_PARALLEL_SEARCH_WORKERS)


class _NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BingRssSearch:
    """Independent keyless Bing web RSS endpoint.

    Bing may return no RSS items or block automated access. Such cases are
    NOT considered success; other sources are tried without user input.
    """
    endpoint = "https://www.bing.com/search"

    def __init__(self, timeout: float = 4.0):
        self.timeout = min(6.0, max(1.0, float(timeout)))

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        q = " ".join(str(query or "").split()).strip()[:400]
        if not q:
            return []
        url = self.endpoint + "?" + parse.urlencode({
            "q": q, "format": "rss", "setlang": "ru",
        })
        req = request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; LeshySearch/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        })
        opener = request.build_opener(request.ProxyHandler(), _NoRedirects())
        try:
            with opener.open(req, timeout=self.timeout) as response:
                raw = response.read(512_001)
            if len(raw) > 512_000:
                raise ValueError("oversized_rss")
            document = ElementTree.fromstring(raw)
        except Exception as exc:
            # Avoid logging a raw URL/query (may contain personal information).
            raise SearchUnavailable("Bing RSS недоступен: " + type(exc).__name__) from None

        if document.tag.lower() != "rss":
            return []
        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in document.findall("./channel/item"):
            href = str(item.findtext("link") or "").strip()
            parsed = parse.urlsplit(href)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or href in seen:
                continue
            seen.add(href)
            title = " ".join(html.unescape(item.findtext("title") or "").split())
            raw_description = str(item.findtext("description") or "")
            summary = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", raw_description)).split())
            results.append(SearchResult(title[:500] or href, href, summary[:1600]))
            if len(results) >= max(1, int(limit)):
                break
        return results


class RacingSearch(FallbackSearch):
    """First successful independent search provider wins; no sequential stalls.

    Thread workers are daemon, slots are global, per-provider socket timeout
    is separately bounded. No request details/URLs are logged in metrics.
    """

    def __init__(self, *providers, deadline_seconds: float = _RACE_DEADLINE_SECONDS):
        super().__init__(*providers)
        self.deadline_seconds = max(1.0, min(float(deadline_seconds), 10.0))

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned:
            return []
        limit = max(1, min(int(limit), 10))
        outcomes: queue.Queue[tuple[str, str, list[SearchResult] | None]] = queue.Queue()
        launched = 0
        started = time.monotonic()

        def worker(provider, name: str) -> None:
            try:
                values = provider.search(cleaned, limit=limit)
                valid = [
                    item for item in (values or [])
                    if isinstance(item, SearchResult)
                    and parse.urlsplit(item.url).scheme in {"https", "http"}
                    and parse.urlsplit(item.url).hostname
                ]
                outcomes.put(("success" if valid else "empty", name, valid[:limit]))
            except Exception:
                outcomes.put(("error", name, None))
            finally:
                _SLOTS.release()

        for provider in self.providers:
            if not _SLOTS.acquire(blocking=False):
                # Other jobs may already be in flight; never create unbounded
                # threads while the user awaits an interactive response.
                continue
            name = type(provider).__name__
            try:
                threading.Thread(
                    target=worker, args=(provider, name),
                    name="leshy-search-" + name,
                    daemon=True,
                ).start()
                launched += 1
            except Exception:
                _SLOTS.release()

        if launched == 0:
            event("search_parallel_saturated", max_workers=_MAX_PARALLEL_SEARCH_WORKERS)
            raise SearchUnavailable("Поисковые источники заняты: слишком много одновременных запросов")

        failures = 0
        while failures < launched:
            remaining = self.deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                outcome, name, results = outcomes.get(timeout=remaining)
            except queue.Empty:
                break
            if outcome == "success" and results:
                event(
                    "search_parallel_provider_selected",
                    provider=name, elapsed_ms=int((time.monotonic() - started) * 1000),
                    results=len(results),
                )
                return results
            failures += 1
        event(
            "search_parallel_exhausted",
            attempted=launched, finished=failures,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        raise SearchUnavailable("Независимые поисковые источники не ответили вовремя или не нашли результатов")


__all__ = ["BingRssSearch", "RacingSearch"]
