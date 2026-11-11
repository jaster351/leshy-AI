from __future__ import annotations

"""Independent, bounded search backends for an interactive Leshy request.

When DuckDuckGo/Yahoo/Startpage time out, no single provider should consume
the entire interactive search budget. The caller gets the first real source,
while other attempts finish on their own within their per-provider timeouts.

Never claim internet success solely because an HTTP endpoint responded 200.
"""

from dataclasses import dataclass
from datetime import datetime
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
_METRICS_LOCK = threading.RLock()
_METRICS = {
    "requests": 0, "successes": 0, "unavailable": 0,
    "last_successful_provider": "", "last_success_at": "",
    "provider_successes": {}, "provider_errors": {}, "provider_empty": {},
}


def _bump(kind: str, provider: str = "") -> None:
    with _METRICS_LOCK:
        if kind in {"requests", "successes", "unavailable"}:
            _METRICS[kind] += 1
        else:
            key = {
                "success": "provider_successes",
                "error": "provider_errors", "empty": "provider_empty",
            }.get(kind)
            if key:
                values = _METRICS[key]
                values[provider] = values.get(provider, 0) + 1
                if kind == "success":
                    _METRICS["last_successful_provider"] = provider
                    _METRICS["last_success_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def search_provider_status() -> dict:
    """Metrics only: never expose query text, source URLs or user identifiers."""
    with _METRICS_LOCK:
        return {
            **{k: v for k, v in _METRICS.items() if not isinstance(v, dict)},
            "provider_successes": dict(_METRICS["provider_successes"]),
            "provider_errors": dict(_METRICS["provider_errors"]),
            "provider_empty": dict(_METRICS["provider_empty"]),
            "parallel": True,
            "deadline_seconds": _RACE_DEADLINE_SECONDS,
            "last_status_scope": "текущий процесс",
        }



# A search endpoint can return valid URLs for an entirely different topic.
# Check the subject before declaring a provider successful (or learning from it).
_SEARCH_FILLER = frozenset("""
расскажи скажи подскажи объясни поясни найди покажи пожалуйста
когда сколько дней осталось осталось какой какая какое какие как где кто почему
будет выйдет выходит выпустят выпустит дата даты выход выхода релиз релиза
последние последний последняя новые новая новый новое сейчас сегодня завтра
информация информации узнать нужно хочу можно про нас мне тебя это этот этой
эти о об от для по с со на в во из к ко у за и или что ли не
tell show explain find please when how many days left remaining
release released date dates latest news new now today tomorrow
what who where why which about the this that with from for and
are is was will can do does of in on to me you
""".split())


def _search_words(value: str) -> list[str]:
    text = str(value or "").casefold().replace("ё", "е")
    # Official release titles sometimes spell the franchise and number out.
    text = re.sub(r"\bgrand\s+theft\s+auto\b", "gta", text)
    text = re.sub(r"\bgta\s+vi\b", "gta 6", text)
    text = re.sub(r"\bgta6\b", "gta 6", text)
    return re.findall(r"[^\W_]+", text, flags=re.UNICODE)



# News is a category, not a named entity: technology headlines seldom repeat
# "нового" / "новостях" and may be published in English.
_NEWS_INTENT = re.compile(r"\b(?:новост\w*|news|нового\s+в\s+(?:it|айти)|latest\s+(?:tech|technology))\b", re.IGNORECASE)
_TECH_TOPIC = re.compile(r"\b(?:it|айти|tech(?:nology)?|технолог\w*|информационн\w*\s+технолог\w*|кибер\w*|cyber\w*|software|софт\w*)\b", re.IGNORECASE)
_TECH_SOURCE_TERMS = frozenset("""
it tech technology technologies technological ai ии искусственный интеллект нейросет
нейросети программирование программисты software hardware сервер процессор видеокарт
security cybersecurity кибербезопасность разработк технолог компьютер linux windows
microsoft google apple nvidia intel amd android ios openai cloud облачн смартфон
робот робототехник data digital chip chips чип чипы processor gpu cpu npu
""".split())
_TECH_NEWS_HOSTS = frozenset((
    "techcrunch.com", "theverge.com", "arstechnica.com", "wired.com",
    "zdnet.com", "bleepingcomputer.com", "theregister.com",
    "habr.com", "3dnews.ru", "ixbt.com", "cnews.ru",
))


def _is_news_query(query: str) -> bool:
    return bool(_NEWS_INTENT.search(str(query or "")))


def _is_tech_news_query(query: str) -> bool:
    return _is_news_query(query) and bool(_TECH_TOPIC.search(str(query or "")))


def _tech_news_hit(item: SearchResult) -> bool:
    tokens = set(_search_words(item.title + " " + item.snippet))
    if tokens & _TECH_SOURCE_TERMS:
        return True
    # Also recognize inflected forms, which exact token matching misses.
    if any(word.startswith(("технолог", "программ", "кибер", "нейросет", "искусственн", "computer", "computing", "security", "software")) for word in tokens):
        return True
    host = (parse.urlsplit(item.url).hostname or "").lower().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in _TECH_NEWS_HOSTS)


def _topic_words(query: str) -> list[str]:
    return [
        word for word in _search_words(query)
        if word not in _SEARCH_FILLER and (len(word) >= 3 or word.isdecimal())
    ]


def _focused_rss_query(query: str) -> str:
    """Remove conversational filler for RSS, preserving the concrete subject."""
    if "site:" in query.lower() or "://" in query:
        return query
    # Broad news queries must be topical and dated; a conversation like
    # "что нового в новостях по IT" is not a useful literal search phrase.
    if _is_news_query(query):
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d")
        if _is_tech_news_query(query):
            return f"IT технологии новости {stamp}"
        return f"главные новости {stamp}" if not any(
            word not in _SEARCH_FILLER for word in _search_words(query)
        ) else query
    all_words = _search_words(query)
    topic = _topic_words(query)
    if not (topic and len(topic) <= 8 and len(all_words) - len(topic) >= 2):
        return query
    focused = " ".join(topic)
    if re.search(r"(?i)\b(выйдет|выходит|выпустят|релиз|release|released)\b", query):
        focused += " дата выхода"
    return focused


def _relevant_search_results(query: str, values: list[SearchResult]) -> list[SearchResult]:
    """Conservative lexical topic gate; never trust an unrelated RSS success.

    Works across languages without introducing an LLM or a network call.
    An ambiguous query without substantive terms remains unfiltered.
    """
    if _is_tech_news_query(query):
        # Article headlines may discuss companies or products without ever
        # saying the word "technology"; avoid false no-results for IT news.
        return [item for item in values if _tech_news_hit(item)]
    if _is_news_query(query):
        # Category-wide news has no mandatory named entity to match. Search
        # provider still needs a real URL; news intent alone is not a subject.
        return values
    topic = _topic_words(query)
    names = [word for word in topic if any(ch.isalpha() for ch in word)]
    if not names:
        return values
    numbers = [word for word in topic if word.isdecimal()]
    # A version/part number can distinguish entirely different releases.
    required_number = numbers[0] if len(numbers) == 1 else None

    def matches(word: str, found: set[str]) -> bool:
        if word in found:
            return True
        if any("а" <= ch <= "я" for ch in word):
            stem = word[:5] if len(word) >= 7 else word[:4] if len(word) >= 5 else word
            return any(candidate.startswith(stem) for candidate in found)
        return False

    filtered: list[SearchResult] = []
    for item in values:
        found = set(_search_words(item.title + " " + item.snippet))
        if not any(matches(word, found) for word in names):
            continue
        if required_number is not None and required_number not in found:
            continue
        filtered.append(item)
    return filtered

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

    def __init__(self, *providers, deadline_seconds: float = _RACE_DEADLINE_SECONDS,
                 hedge_delay_seconds: float = 0.0):
        super().__init__(*providers)
        self.deadline_seconds = max(1.0, min(float(deadline_seconds), 10.0))
        # When a real Chromium provider is first, wait briefly for its result
        # before waking independent HTTP fallbacks. Normal RacingSearch stays
        # fully parallel by default for backwards compatibility.
        self.hedge_delay_seconds = max(0.0, min(float(hedge_delay_seconds), 3.0))

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned:
            return []
        limit = max(1, min(int(limit), 10))
        outcomes: queue.Queue[tuple[str, str, list[SearchResult] | None]] = queue.Queue()
        launched = 0
        started = time.monotonic()
        _bump("requests")

        def worker(provider, name: str) -> None:
            try:
                provider_query = _focused_rss_query(cleaned) if isinstance(provider, BingRssSearch) or _is_tech_news_query(cleaned) else cleaned
                values = provider.search(provider_query, limit=limit)
                valid = [
                    item for item in (values or [])
                    if isinstance(item, SearchResult)
                    and parse.urlsplit(item.url).scheme in {"https", "http"}
                    and parse.urlsplit(item.url).hostname
                ]
                relevant = _relevant_search_results(cleaned, valid)
                if valid and not relevant:
                    event("search_parallel_irrelevant_results", provider=name, discarded=len(valid))
                outcome = "success" if relevant else "empty"
                _bump(outcome, name)
                outcomes.put((outcome, name, relevant[:limit]))
            except Exception:
                _bump("error", name)
                outcomes.put(("error", name, None))
            finally:
                _SLOTS.release()

        def launch(provider) -> bool:
            nonlocal launched
            if not _SLOTS.acquire(blocking=False):
                return False
            name = type(provider).__name__
            try:
                threading.Thread(
                    target=worker, args=(provider, name),
                    name="leshy-search-" + name, daemon=True,
                ).start()
                launched += 1
                return True
            except Exception:
                _SLOTS.release()
                return False

        def receive(timeout: float) -> list[SearchResult] | None:
            nonlocal finished
            try:
                outcome, name, results = outcomes.get(timeout=timeout)
            except queue.Empty:
                return None
            if outcome == "success" and results:
                _bump("successes")
                event(
                    "search_parallel_provider_selected", provider=name,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    results=len(results),
                )
                return results
            finished += 1
            return None

        providers = tuple(self.providers)
        finished = 0
        offset = 0
        if self.hedge_delay_seconds > 0 and providers:
            if launch(providers[0]):
                offset = 1
                initial = receive(min(
                    self.hedge_delay_seconds,
                    max(0.001, self.deadline_seconds - (time.monotonic() - started)),
                ))
                if initial:
                    return initial
            # A failed first provider should not force the full hedge delay.

        for provider in providers[offset:]:
            launch(provider)

        if launched == 0:
            _bump("unavailable")
            event("search_parallel_saturated", max_workers=_MAX_PARALLEL_SEARCH_WORKERS)
            raise SearchUnavailable("Поисковые источники заняты: слишком много одновременных запросов")

        while finished < launched:
            remaining = self.deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            result = receive(remaining)
            if result:
                return result

        _bump("unavailable")
        event(
            "search_parallel_exhausted",
            attempted=launched, finished=finished,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        raise SearchUnavailable("Независимые поисковые источники не ответили вовремя или не нашли результатов")


__all__ = ["BingRssSearch", "RacingSearch", "search_provider_status"]
