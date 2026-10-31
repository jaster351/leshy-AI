from __future__ import annotations

from dataclasses import dataclass
import html
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib import error, parse, request

from .knowledge import KnowledgeBase
from .storage import Storage, utcnow


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
            raise RuntimeError(f"SearXNG недоступен: {exc}") from exc
        return [
            SearchResult(str(x.get("title", x.get("url", ""))), str(x.get("url", "")), str(x.get("content", "")))
            for x in body.get("results", [])[:limit]
            if str(x.get("url", "")).startswith(("http://", "https://"))
        ]


class DdgsSearch:
    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        try:
            from ddgs import DDGS
            rows = DDGS().text(query, max_results=limit)
        except Exception as exc:
            raise RuntimeError(f"DDGS-поиск недоступен: {exc}") from exc
        out: list[SearchResult] = []
        for item in rows or []:
            url = str(item.get("href") or item.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            out.append(SearchResult(str(item.get("title") or url), url, str(item.get("body") or item.get("content") or "")))
            if len(out) >= limit:
                break
        return out


class FallbackSearch:
    def __init__(self, *providers):
        self.providers = providers

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        errors: list[str] = []
        for provider in self.providers:
            try:
                results = provider.search(query, limit=limit)
                if results:
                    return results
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("Все поисковые провайдеры недоступны: " + "; ".join(errors))


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
        req = request.Request(url, headers={"User-Agent": "LeshyResearchBot/1.10.3"})
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

    def live_query(self, query: str, reason: str = "запрос пользователя", limit: int = 5) -> dict:
        """Быстрый интернет-поиск для ответа без тяжёлой синхронной верификации.

        Сниппеты сразу сохраняются как candidate-знания, поэтому база реально
        растёт уже во время live-search. Полное чтение страниц и верификация
        остаются отдельной queued-задачей и могут выполняться worker'ом в фоне.
        """
        query = " ".join(str(query).split()).strip()
        if not query:
            return {"query": query, "status": "empty", "results": [], "accepted_claims": 0}

        task_id = self.enqueue(query, reason=f"live-search: {reason}")
        try:
            results = self.search_provider.search(query, limit=limit)
        except Exception as exc:
            self.storage.audit("research.live_search_failed", {"task_id": task_id, "query": query, "error": str(exc)[:1000]})
            return {"task_id": task_id, "query": query, "status": "search_failed", "error": str(exc), "results": [], "accepted_claims": 0}

        accepted = 0
        serialized: list[dict] = []
        for result in results:
            snippet = " ".join(str(result.snippet).split()).strip()
            title = " ".join(str(result.title).split()).strip()
            serialized.append({"title": title, "url": result.url, "snippet": snippet})
            if not snippet:
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
                "verification_queued": True,
            },
        )
        return {
            "task_id": task_id,
            "query": query,
            "status": "queued_verification",
            "results": serialized,
            "accepted_claims": accepted,
            "verification_queued": True,
        }

    def _claim(self, task_id: int | None = None):
        with self.storage.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if task_id is None:
                task = conn.execute("SELECT * FROM research_tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            else:
                task = conn.execute("SELECT * FROM research_tasks WHERE id=? AND status='queued'", (task_id,)).fetchone()
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

    def run_next(self) -> dict | None:
        claimed = self._claim()
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
