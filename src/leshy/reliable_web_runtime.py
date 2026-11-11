from __future__ import annotations

import html
import os
from html.parser import HTMLParser
import re
import time
from types import MethodType
from urllib import parse, request

from .diagnostics import event, problem
from .llm import ChatMessage
from .research import DdgsSearch, FallbackSearch, SearchResult, SearxngSearch
from .search_parallel import BingRssSearch, RacingSearch
from .search_chromium import ChromiumSearch
from .research_policy import research_reason
from .realtime_chat import RealtimeChatManager
from .storage import utcnow


class _DuckDuckGoHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._title_depth = 0
        self._snippet_depth = 0
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._href = ""
        self._snippet_target: int | None = None

    @staticmethod
    def _classes(attrs) -> set[str]:
        for key, value in attrs:
            if key == "class":
                return {part for part in str(value or "").split() if part}
        return set()

    @staticmethod
    def _attr(attrs, name: str) -> str:
        for key, value in attrs:
            if key == name:
                return str(value or "")
        return ""

    def handle_starttag(self, tag, attrs):
        classes = self._classes(attrs)
        if tag == "a" and "result__a" in classes:
            self._title_depth = 1
            self._title_parts = []
            self._href = self._attr(attrs, "href")
            return
        if self._title_depth:
            self._title_depth += 1
        if "result__snippet" in classes:
            self._snippet_depth = 1
            self._snippet_parts = []
            self._snippet_target = len(self.results) - 1 if self.results else None
            return
        if self._snippet_depth:
            self._snippet_depth += 1

    def handle_endtag(self, tag):
        if self._title_depth:
            self._title_depth -= 1
            if self._title_depth == 0:
                title = " ".join("".join(self._title_parts).split()).strip()
                if title and self._href:
                    self.results.append({"title": html.unescape(title), "href": html.unescape(self._href), "snippet": ""})
                self._title_parts = []
                self._href = ""
        if self._snippet_depth:
            self._snippet_depth -= 1
            if self._snippet_depth == 0:
                snippet = " ".join("".join(self._snippet_parts).split()).strip()
                if snippet and self._snippet_target is not None and 0 <= self._snippet_target < len(self.results):
                    self.results[self._snippet_target]["snippet"] = html.unescape(snippet)
                self._snippet_parts = []
                self._snippet_target = None

    def handle_data(self, data):
        if self._title_depth:
            self._title_parts.append(data)
        if self._snippet_depth:
            self._snippet_parts.append(data)


class DuckDuckGoHtmlSearch:
    """Безключевой резервный поиск, не требующий стороннего Python-пакета."""

    endpoint = "https://html.duckduckgo.com/html/"

    @staticmethod
    def _real_url(href: str) -> str:
        value = str(href or "").strip()
        if value.startswith("//"):
            value = "https:" + value
        parsed = parse.urlparse(value)
        if parsed.hostname and "duckduckgo.com" in parsed.hostname:
            args = parse.parse_qs(parsed.query)
            target = str((args.get("uddg") or [""])[0]).strip()
            if target.startswith(("http://", "https://")):
                return target
        return value

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        q = " ".join(str(query or "").split()).strip()
        if not q:
            return []
        url = self.endpoint + "?" + parse.urlencode({"q": q, "kl": "ru-ru"})
        req = request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Leshy-AI/1",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            },
        )
        try:
            # Отводим не более 6 секунд резервному HTML-поиску:
            # общий бюджет интерактивного поиска — 12 секунд.
            with request.urlopen(req, timeout=6) as response:
                raw = response.read(1_500_000)
        except Exception as exc:
            raise RuntimeError(f"DuckDuckGo HTML недоступен: {exc}") from exc
        parser = _DuckDuckGoHtmlParser()
        parser.feed(raw.decode("utf-8", "replace"))
        out: list[SearchResult] = []
        seen: set[str] = set()
        for item in parser.results:
            target = self._real_url(item.get("href", ""))
            if not target.startswith(("http://", "https://")) or target in seen:
                continue
            seen.add(target)
            out.append(SearchResult(item.get("title", target), target, item.get("snippet", "")))
            if len(out) >= max(1, int(limit)):
                break
        return out


_INSTALLED = False
_ORIGINAL_APP_INIT = None
_ORIGINAL_LIVE_SEARCH = None
_ORIGINAL_ANSWER_NEEDS_RESEARCH = None
_ORIGINAL_BUILD_CONTEXT = None
_ORIGINAL_PENDING_SESSIONS = None


def _configure_app_search(app) -> None:
    """Prefer real isolated Chromium, hedge against its temporary failure.

    The search provider's normal user agent and cookies belong to the real
    Chromium session. After a short delay, independent HTTP providers become
    available, even when Chromium meets a CAPTCHA or cannot start.
    """
    provider = getattr(getattr(app, "research", None), "search_provider", None)
    if provider is not None and not bool(getattr(provider, "_leshy_reliable_web", False)):
        original = (
            list(provider.providers) if isinstance(provider, FallbackSearch)
            else [provider]
        )
        fast = [item for item in original if not isinstance(item, DdgsSearch)]
        for item in fast:
            # SearXNG can be configured with a 20-second request timeout.
            # Never let a single local service hold a worker for 20 seconds.
            if isinstance(item, SearxngSearch):
                item.timeout = min(float(item.timeout), 4.0)
        if not any(isinstance(item, DuckDuckGoHtmlSearch) for item in fast):
            fast.append(DuckDuckGoHtmlSearch())
        if not any(isinstance(item, BingRssSearch) for item in fast):
            fast.append(BingRssSearch(timeout=4.0))
        # Full DDGS auto/backends used to occupy many seconds *per source*
        # and kept detached worker slots busy after the chat timed out.
        # Preserve independent engines, but cap to a short bounded subset.
        slow = [
            DdgsSearch(timeout=3, backends=("google", "mojeek", "yandex"))
            for item in original if isinstance(item, DdgsSearch)
        ]
        chromium_enabled = str(os.getenv("LESHY_CHROMIUM_SEARCH_ENABLED", "1")).strip().lower() not in {
            "0", "false", "off", "no",
        }
        if chromium_enabled:
            try:
                fast.insert(0, ChromiumSearch())
            except ValueError as exc:
                problem("chromium_search_config_invalid", type(exc).__name__)
        combined = RacingSearch(
            *fast, *slow, deadline_seconds=8.0,
            hedge_delay_seconds=1.4 if chromium_enabled and isinstance(fast[0], ChromiumSearch) else 0.0,
        )
        setattr(combined, "_leshy_reliable_web", True)
        app.research.search_provider = combined
        event(
            "reliable_web_search_ready",
            providers=" || ".join(type(item).__name__ for item in combined.providers),
            parallel=True,
            preferred="ChromiumSearch" if chromium_enabled else "HTTP-only",
            hedge_delay_seconds=combined.hedge_delay_seconds,
            deadline_seconds=8.0,
        )
    # Recovery должен работать только с сообщениями, существовавшими до старта
    # текущего процесса. Новые Telegram updates нельзя одновременно забирать
    # polling-потоком и startup recovery.
    if not getattr(app, "startup_recovery_cutoff", ""):
        app.startup_recovery_cutoff = utcnow()


def install_reliable_web_runtime(app_class, app=None) -> None:
    """Общий исправляющий слой Web/Voice/Telegram/recovery интернет-поиска."""
    global _INSTALLED, _ORIGINAL_APP_INIT, _ORIGINAL_LIVE_SEARCH
    global _ORIGINAL_ANSWER_NEEDS_RESEARCH, _ORIGINAL_BUILD_CONTEXT, _ORIGINAL_PENDING_SESSIONS
    # Для уже существующего LeshyApp (например, прямой вызов make_handler)
    # нельзя рассчитывать только на monkeypatch его __init__.
    if app is not None:
        _configure_app_search(app)
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_APP_INIT = app_class.__init__
    _ORIGINAL_LIVE_SEARCH = app_class.live_search
    _ORIGINAL_ANSWER_NEEDS_RESEARCH = app_class._answer_needs_research
    _ORIGINAL_BUILD_CONTEXT = RealtimeChatManager._build_context

    def app_init(self, *args, **kwargs):
        _ORIGINAL_APP_INIT(self, *args, **kwargs)
        _configure_app_search(self)

    def live_search(self, query: str, *, limit: int = 5, reason: str = "прямой запрос пользователя") -> dict:
        result = _ORIGINAL_LIVE_SEARCH(self, query, limit=limit, reason=reason)
        status = str(result.get("status") or "")
        if status == "search_failed":
            error = str(result.get("error") or "поисковый backend не сообщил причину")
            raise RuntimeError(error)
        return result

    def answer_needs_research(answer: str) -> bool:
        if _ORIGINAL_ANSWER_NEEDS_RESEARCH(answer):
            return True
        value = " ".join(str(answer or "").lower().split())
        markers = (
            "не могу искать информацию в интернете",
            "не могу искать в интернете",
            "не умею искать информацию в интернете",
            "не умею искать в интернете",
            "не могу обновлять свою базу",
            "не могу обновлять базу данных",
            "я являюсь статическим ai-ассистентом",
            "я статический ai-ассистент",
            "ограничена знаниями, полученными до",
            "ограничен знаниями, полученными до",
        )
        return any(marker in value for marker in markers)

    def build_context(self, message, owner_id, rows, *, search_plan=None):
        plan = search_plan or self._search_plan(message)
        messages, sources, attempted = _ORIGINAL_BUILD_CONTEXT(
            self,
            message,
            owner_id,
            rows,
            search_plan=plan,
        )
        reason = str(plan.get("reason") or "")
        explicit = reason == "явный запрос интернет-поиска"
        if attempted and sources:
            messages.append(ChatMessage(
                "system",
                "Веб-поиск уже реально выполнен приложением, получены результаты. "
                "Сниппеты могут быть сохранены как candidate-знания только если включено обучение. "
                "Не утверждай, что поиск недоступен, если источники переданы. "
                "Ответь по найденным источникам; при недостатке доказательств прямо укажи неопределённость.",
            ))
        elif attempted and explicit:
            search_error = ""
            for item in messages:
                content = str(getattr(item, "content", "") or "")
                marker = "Текущий веб-поиск временно не выполнен:"
                if marker in content:
                    search_error = content.split(marker, 1)[-1].strip()
                    break
            if search_error:
                # Не переводим всю Telegram-генерацию в ошибку: верхний слой
                # покажет настоящую причину недоступности поиска, без LLM.
                event("live_search_unavailable_reported", error_type="provider_error")
            else:
                messages.append(ChatMessage(
                    "system",
                    "Интернет-поиск выполнен, но подходящих результатов не найдено. Не утверждай, "
                    "что поиск недоступен или что источник подтверждает неизвестный факт.",
                ))
        return messages, sources, attempted

    app_class.__init__ = app_init
    app_class.live_search = live_search
    app_class._answer_needs_research = staticmethod(answer_needs_research)
    RealtimeChatManager._build_context = build_context

    # Защита от дублей Telegram после перезапуска: startup recovery не должен
    # подхватывать сообщения, пришедшие уже после запуска текущего процесса.
    try:
        from . import startup_recovery
        _ORIGINAL_PENDING_SESSIONS = startup_recovery._pending_sessions

        def pending_sessions(app, *, limit: int = 50):
            rows = _ORIGINAL_PENDING_SESSIONS(app, limit=limit)
            cutoff = str(getattr(app, "startup_recovery_cutoff", "") or "")
            if not cutoff:
                return rows
            filtered = [row for row in rows if str(row.get("created_at") or "") < cutoff]
            skipped = len(rows) - len(filtered)
            if skipped:
                event("startup_recovery_live_messages_excluded", skipped=skipped)
            return filtered

        startup_recovery._pending_sessions = pending_sessions
    except Exception as exc:
        problem("startup_recovery_cutoff_install_failed", str(exc))

    event("reliable_web_runtime_installed")


__all__ = ["DuckDuckGoHtmlSearch", "install_reliable_web_runtime"]
