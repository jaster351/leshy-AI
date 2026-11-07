from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib import error, parse, request

from .knowledge import KnowledgeBase
from .storage import Storage, utcnow
from .version import VERSION


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._blocked += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._blocked:
            self._blocked -= 1

    def handle_data(self, data):
        if not self._blocked:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchUnavailable(RuntimeError):
    """Все доступные поисковые каналы временно недоступны или пусты."""


class SearxngSearch:
    def __init__(self, base_url: str, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        url = f"{self.base_url}/search?" + parse.urlencode({"q": query, "format": "json", "language": "all"})
        try:
            with request.urlopen(url, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SearchUnavailable(f"SearXNG недоступен: {exc}") from exc
        return [
            SearchResult(str(x.get("title", x.get("url", ""))), str(x.get("url", "")), str(x.get("content", "")))
            for x in body.get("results", [])[:limit]
            if str(x.get("url", "")).startswith(("http://", "https://"))
        ]


class DdgsSearch:
    """Безключевой метапоиск с быстрым переключением между независимыми backend'ами.

    `backend="auto"` иногда выбирает один медленный/заблокированный источник
    (например Brave) и роняет весь запрос. Здесь каждый backend имеет короткий
    timeout и сбой одного источника не мешает попробовать следующий.
    """

    DEFAULT_BACKENDS = (
        "duckduckgo",
        "bing",
        "google",
        "mojeek",
        "startpage",
        "yandex",
        "yahoo",
        "wikipedia",
        "brave",
    )

    def __init__(self, timeout: int = 5, backends: tuple[str, ...] | None = None):
        self.timeout = max(2, min(int(timeout), 15))
        self.backends = tuple(backends or self.DEFAULT_BACKENDS)

    @staticmethod
    def _rows_to_results(rows, limit: int) -> list[SearchResult]:
        out: list[SearchResult] = []
        seen: set[str] = set()
        for item in rows or []:
            url = str(item.get("href") or item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            out.append(
                SearchResult(
                    str(item.get("title") or url),
                    url,
                    str(item.get("body") or item.get("content") or ""),
                )
            )
            if len(out) >= limit:
                break
        return out

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        try:
            from ddgs import DDGS
        except Exception as exc:
            raise SearchUnavailable(f"DDGS-пакет недоступен: {exc}") from exc

        errors: list[str] = []
        for backend in self.backends:
            try:
                rows = DDGS(timeout=self.timeout).text(
                    query,
                    max_results=max(1, int(limit)),
                    backend=backend,
                )
                results = self._rows_to_results(rows, max(1, int(limit)))
                if results:
                    return results
                errors.append(f"{backend}: 0 результатов")
            except Exception as exc:
                errors.append(f"{backend}: {exc}")
        raise SearchUnavailable("DDGS backend-цепочка недоступна: " + "; ".join(errors))


class FallbackSearch:
    def __init__(self, *providers):
        self.providers = providers

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        errors: list[str] = []
        for provider in self.providers:
            provider_name = type(provider).__name__
            try:
                results = provider.search(query, limit=limit)
                if results:
                    return results
                errors.append(f"{provider_name}: 0 результатов")
            except Exception as exc:
                errors.append(f"{provider_name}: {exc}")
        raise SearchUnavailable("Все поисковые провайдеры недоступны или не дали результатов: " + "; ".join(errors))


class SafeWebFetcher:
    MAX_BYTES = 1_500_000

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Разрешены только публичные HTTP(S) URL")
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"}:
            raise ValueError("Локальные адреса запрещены исследовательскому контуру")
        try:
            addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"Не удалось разрешить имя источника: {host}") from exc
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                raise ValueError(f"Непубличный адрес источника запрещён: {ip}")

    class _ValidatingRedirectHandler(request.HTTPRedirectHandler):
        def __init__(self, validator):
            super().__init__()
            self.validator = validator

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            self.validator(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    def fetch_text(self, url: str, timeout: int = 20) -> str:
        self._validate_public_url(url)
        opener = request.build_opener(self._ValidatingRedirectHandler(self._validate_public_url))
        req = request.Request(url, headers={"User-Agent": f"LeshyResearchBot/{VERSION}"})
        try:
            with opener.open(req, timeout=timeout) as response:
                self._validate_public_url(response.geturl())
                content_type = response.headers.get("Content-Type", "")
                if "text" not in content_type and "json" not in content_type and "html" not in content_type:
                    raise ValueError(f"Неподдерживаемый Content-Type: {content_type}")
                raw = response.read(self.MAX_BYTES + 1)
        except error.URLError as exc:
            raise RuntimeError(f"Не удалось загрузить {url}: {exc}") from exc
        if len(raw) > self.MAX_BYTES:
            raise ValueError("Страница превышает безопасный лимит")
        text = raw.decode("utf-8", errors="replace")
        if "html" in content_type:
            parser = _TextExtractor()
            parser.feed(text)
            text = parser.text()
        else:
            text = html.unescape(text)
        return " ".join(text.split())[:200_000]


PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "system prompt",
    "developer message",
    "игнорируй предыдущие инструкции",
    "забудь предыдущие инструкции",
    "системный промпт",
)


class ResearchEngine:
    SEARCH_RETRY_SECONDS = 60

    def __init__(
        self,
        storage: Storage,
        knowledge: KnowledgeBase,
        search,
        fetcher: SafeWebFetcher | None = None,
        semantic_verifier=None,
        activity=None,
        safety_gate=None,
        browser_fetcher=None,
    ):
        self.storage = storage
        self.knowledge = knowledge
        self.search_provider = search
        self.fetcher = fetcher or SafeWebFetcher()
        self.semantic_verifier = semantic_verifier
        self.activity = activity
        self.safety_gate = safety_gate
        self.browser_fetcher = browser_fetcher

    def enqueue(self, query: str, reason: str = "обнаружен пробел в знаниях") -> int:
        with self.storage.connection() as conn:
            cur = conn.execute(
                "INSERT INTO research_tasks(query,reason,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (query.strip(), reason, "queued", utcnow(), utcnow()),
            )
            task_id = int(cur.lastrowid)
        self.storage.audit("research.queued", {"id": task_id, "query": query, "reason": reason})
        return task_id

    def _queue_retry(self, task_id: int, query: str, exc: Exception, *, live: bool = False) -> dict:
        message = str(exc)[:1000]
        now = utcnow()
        with self.storage.connection() as conn:
            if live:
                conn.execute(
                    """
                    UPDATE research_tasks
                    SET status='queued',
                        attempts=CASE WHEN attempts < 1 THEN 1 ELSE attempts END,
                        updated_at=?,
                        last_error=?
                    WHERE id=? AND status='queued'
                    """,
                    (now, message, task_id),
                )
            else:
                conn.execute(
                    "UPDATE research_tasks SET status='queued',updated_at=?,last_error=? WHERE id=? AND status='running'",
                    (now, message, task_id),
                )
        event_name = "research.live_search_retry_queued" if live else "research.retry_queued"
        self.storage.audit(
            event_name,
            {
                "id": task_id,
                "query": query,
                "error": message,
                "retry_after_seconds": self.SEARCH_RETRY_SECONDS,
            },
        )
        return {
            "task_id": task_id,
            "query": query,
            "status": "retry_queued",
            "error": message,
            "results": [],
            "accepted_claims": 0,
            "retry_after_seconds": self.SEARCH_RETRY_SECONDS,
        }

    def live_query(self, query: str, reason: str = "запрос пользователя", limit: int = 5) -> dict:
        """Быстрый интернет-поиск для ответа без тяжёлой синхронной верификации.

        Поиск всегда возвращает результаты для текущего ответа. Только при
        включённом самообучении сниппеты сохраняются как candidate-знания,
        а проверка страниц ставится в очередь. Без обучения база не меняется.
        """
        query = " ".join(str(query).split()).strip()
        if not query:
            return {"query": query, "status": "empty", "results": [], "accepted_claims": 0}

        learning_allowed = bool(getattr(self, "auto_learning_enabled", lambda: True)())
        task_id = self.enqueue(query, reason=f"live-search: {reason}") if learning_allowed else None
        try:
            results = self.search_provider.search(query, limit=limit)
        except SearchUnavailable as exc:
            if not learning_allowed:
                return {"query": query, "status": "search_failed", "error": str(exc), "results": [], "accepted_claims": 0}
            return self._queue_retry(task_id, query, exc, live=True)
        except Exception as exc:
            self.storage.audit("research.live_search_failed", {"task_id": task_id, "query": query, "error": str(exc)[:1000]})
            return {"task_id": task_id, "query": query, "status": "search_failed", "error": str(exc), "results": [], "accepted_claims": 0}

        accepted = 0
        serialized: list[dict] = []
        for result in results:
            snippet = " ".join(str(result.snippet).split()).strip()
            title = " ".join(str(result.title).split()).strip()
            serialized.append({"title": title, "url": result.url, "snippet": snippet})
            if not snippet or not learning_allowed:
                continue
            if any(marker in snippet.lower() for marker in PROMPT_INJECTION_MARKERS):
                self.storage.audit("research.live_source_quarantined", {"task_id": task_id, "url": result.url})
                continue
            claims = self._extract_candidate_claims(snippet)
            if not claims and len(snippet) >= 25:
                claims = [snippet[:420]]
            for claim in claims[:2]:
                self.knowledge.add_candidate(
                    topic=query,
                    claim=claim,
                    source_url=result.url,
                    source_title=title,
                    source_type="live-search-snippet",
                    metadata_json={
                        "provisional": True,
                        "verification": "queued",
                        "captured_via": "live-search",
                    },
                )
                accepted += 1

        self.storage.audit(
            "research.live_search_completed",
            {
                "task_id": task_id,
                "query": query,
                "results": len(serialized),
                "accepted_claims": accepted,
                "verification_queued": learning_allowed,
            },
        )
        return {
            "task_id": task_id,
            "query": query,
            "status": "queued_verification" if learning_allowed else "search_complete",
            "results": serialized,
            "accepted_claims": accepted,
            "verification_queued": learning_allowed,
        }

    def _retry_before(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(seconds=self.SEARCH_RETRY_SECONDS)).isoformat()

    def _claim(self, task_id: int | None = None, *, manual_only: bool = False):
        retry_before = self._retry_before()
        with self.storage.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if task_id is None:
                # Автономное обучение может быть выключено. Явные задания
                # пользователя при этом разрешены, но фоновую очередь НЕ трогаем.
                task = conn.execute(
                    """
                    SELECT * FROM research_tasks
                    WHERE status='queued' AND (attempts=0 OR updated_at<=?)
                      AND (?=0 OR reason='тема пользователя'
                           OR reason LIKE 'явная команда пользователя:%')
                    ORDER BY id
                    LIMIT 1
                    """,
                    (retry_before, int(bool(manual_only))),
                ).fetchone()
            else:
                task = conn.execute(
                    """
                    SELECT * FROM research_tasks
                    WHERE id=? AND status='queued' AND (attempts=0 OR updated_at<=?)
                    """,
                    (task_id, retry_before),
                ).fetchone()
            if not task:
                return None
            claimed = int(task["id"])
            changed = conn.execute(
                "UPDATE research_tasks SET status='running',attempts=attempts+1,updated_at=? WHERE id=? AND status='queued'",
                (utcnow(), claimed),
            ).rowcount
            return {"id": claimed, "query": str(task["query"])} if changed == 1 else None

    def run_query(self, query: str, reason: str = "автономное исследование") -> dict:
        task_id = self.enqueue(query, reason)
        claimed = self._claim(task_id)
        return self._execute(claimed["id"], claimed["query"]) if claimed else {"task_id": task_id, "query": query, "status": "not_claimed"}

    def run_next(self, *, manual_only: bool = False) -> dict | None:
        claimed = self._claim(manual_only=manual_only)
        return self._execute(claimed["id"], claimed["query"]) if claimed else None

    def _execute(self, task_id: int, query: str) -> dict:
        try:
            results = self.search_provider.search(query, limit=8)
            accepted = 0
            quarantined = 0
            for result in results:
                text = f"{result.snippet} "
                source_type = "search-snippet"
                try:
                    source_text = self.fetcher.fetch_text(result.url)[:12000]
                    text += source_text
                    source_type = "web"
                except Exception as exc:
                    self.storage.audit("research.source_fetch_failed", {"task_id": task_id, "url": result.url, "error": str(exc)[:500]})
                if any(marker in text.lower() for marker in PROMPT_INJECTION_MARKERS):
                    quarantined += 1
                    continue
                safety = None
                if self.safety_gate:
                    safety = self.safety_gate.classify(text, title=result.title, url=result.url)
                    if not safety.allow_learning:
                        quarantined += 1
                        continue
                metadata = {
                    "learning_safety": getattr(safety, "category", "unclassified"),
                    "learning_safety_confidence": getattr(safety, "confidence", 0.0),
                }
                for claim in self._extract_candidate_claims(text)[:5]:
                    self.knowledge.add_candidate(
                        topic=query,
                        claim=claim,
                        source_url=result.url,
                        source_title=result.title,
                        source_type=source_type,
                        metadata_json=metadata,
                    )
                    accepted += 1
            verification = self.knowledge.verify_topic(query)
            if self.semantic_verifier:
                verification.update(self.semantic_verifier.verify_topic(query))
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE research_tasks SET status='done',updated_at=?,last_error=NULL WHERE id=? AND status='running'",
                    (utcnow(), task_id),
                )
            result = {"task_id": task_id, "query": query, "accepted_claims": accepted, "quarantined_sources": quarantined, **verification}
            self.storage.audit("research.completed", {"id": task_id, **result})
            return result
        except SearchUnavailable as exc:
            return self._queue_retry(task_id, query, exc, live=False)
        except Exception as exc:
            with self.storage.connection() as conn:
                conn.execute(
                    "UPDATE research_tasks SET status='failed',updated_at=?,last_error=? WHERE id=?",
                    (utcnow(), str(exc)[:1000], task_id),
                )
            self.storage.audit("research.failed", {"id": task_id, "error": str(exc)[:1000]})
            return {"task_id": task_id, "query": query, "error": str(exc)}

    @staticmethod
    def _extract_candidate_claims(text: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for chunk in re.split(r"(?<=[.!?])\s+", text):
            chunk = " ".join(chunk.split()).strip()
            if 45 <= len(chunk) <= 420 and chunk not in seen:
                seen.add(chunk)
                out.append(chunk)
            if len(out) >= 30:
                break
        return out
