from __future__ import annotations

from typing import Any

from .diagnostics import event
from .inference_scheduler import get_inference_scheduler
from .llm import ChatMessage
from .realtime_chat import RealtimeChatManager
from .research import DdgsSearch
from .adaptive_internet_policy import adaptive_research_reason, is_offline_creative_request
from .temporal_grounding import ground_search_query

# Подтверждено runtime-логом ddgs 2026-11-02. Несуществующие для текущего
# пакета backend'ы (bing/google/yandex) нельзя передавать: ddgs молча уходит в
# auto и тем самым ломает детерминированный failover.
SUPPORTED_DDGS_BACKENDS = (
    "duckduckgo",
    "yahoo",
    "grokipedia",
    "wikipedia",
    "startpage",
    "mojeek",
    "brave",
)

# Qwen3-8B на 16 ГБ RAM в runtime ограничивается контекстом llama-server 4096.
# Persona добавляется позже streaming backend'ом, поэтому realtime-контекст
# держим компактным и оставляем запас под identity prompt и ответ модели.
REALTIME_CONTEXT_CHAR_BUDGET = 2600

# Интерактивный запрос не должен бесконечно висеть за уже занятой LLM.
# 90 секунд заметно больше подтверждённого runtime-логом ожидания Web (39 с),
# но исключает многоминутное зависание при потерянном/зависшем активном слоте.
INTERACTIVE_QUEUE_TIMEOUT_SECONDS = 90.0

_INSTALLED = False
_ORIGINAL_SEARCH_PLAN = None
_ORIGINAL_BUILD_CONTEXT = None
_ORIGINAL_LIVE_SEARCH = None


def _clip(text: str, limit: int, *, preserve_ends: bool = False) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    if limit < 80:
        return value[:limit]
    if preserve_ends:
        marker = "\n[…контекст сокращён…]\n"
        room = max(20, limit - len(marker))
        head = room // 2
        tail = room - head
        return value[:head] + marker + value[-tail:]
    return value[: max(0, limit - 1)] + "…"


def compact_realtime_messages(messages: list[ChatMessage], *, max_chars: int = REALTIME_CONTEXT_CHAR_BUDGET) -> list[ChatMessage]:
    """Собирает небольшой prompt до запроса к llama-server.

    Приоритет: текущий вопрос -> свежий web-search -> главный system prompt ->
    последние реплики -> память/прочие system-блоки. Это предотвращает сначала
    HTTP 400 `context size`, а затем дорогой повтор большого prompt.
    """
    items = list(messages)
    budget = max(800, int(max_chars))
    before = sum(len(str(getattr(item, "content", "") or "")) for item in items)
    if before <= budget:
        return items

    latest_user = next((i for i in range(len(items) - 1, -1, -1) if items[i].role == "user"), None)
    first_system = next((i for i, item in enumerate(items) if item.role == "system"), None)
    search_markers = (
        "Результаты текущего веб-поиска",
        "Веб-поиск уже реально выполнен",
        "Текущий веб-поиск временно не выполнен",
    )
    search_systems = [
        i for i, item in enumerate(items)
        if item.role == "system" and any(marker in str(item.content) for marker in search_markers)
    ]

    selected: dict[int, ChatMessage] = {}
    remaining = budget

    def take(index: int | None, cap: int, *, preserve_ends: bool = False) -> None:
        nonlocal remaining
        if index is None or index in selected or remaining <= 0:
            return
        item = items[index]
        allowed = min(max(1, int(cap)), remaining)
        content = _clip(str(item.content), allowed, preserve_ends=preserve_ends)
        selected[index] = ChatMessage(str(item.role), content)
        remaining -= len(content)

    # Никогда не отдаём старой истории бюджет раньше текущего вопроса.
    take(latest_user, 1200, preserve_ends=True)
    for index in reversed(search_systems):
        take(index, 1100)
    take(first_system, 450)

    # Затем самые свежие сообщения диалога; старые автоматически выпадают.
    for index in range(len(items) - 1, -1, -1):
        if index in selected or items[index].role == "system":
            continue
        take(index, 420, preserve_ends=True)

    # Остаток отдаём памяти/trusted/system-инструкциям.
    for index in range(len(items) - 1, -1, -1):
        if index in selected:
            continue
        take(index, 320)

    result = [selected[index] for index in sorted(selected)]
    after = sum(len(item.content) for item in result)
    event(
        "realtime_context_compacted",
        before_chars=before,
        after_chars=after,
        before_messages=len(items),
        after_messages=len(result),
        budget_chars=budget,
    )
    return result


def _patch_provider(provider: Any) -> None:
    if isinstance(provider, DdgsSearch):
        provider.backends = SUPPORTED_DDGS_BACKENDS
        return
    for child in tuple(getattr(provider, "providers", ()) or ()):
        _patch_provider(child)


def _base_search_plan(manager, message: str) -> dict[str, Any]:
    """Минимальный совместимый план для knowledge backend без freshness API."""
    knowledge = manager.app.knowledge
    trusted_search = getattr(knowledge, "search", None)
    candidate_search = getattr(knowledge, "search_candidates", None)
    trusted = list(trusted_search(message, trusted_only=True, limit=5) or []) if callable(trusted_search) else []
    candidates = list(candidate_search(message, limit=5) or []) if callable(candidate_search) else []
    reason = adaptive_research_reason(message, trusted, candidates)
    marker_router = getattr(manager.app, "_needs_live_search", None)
    needed = bool(reason or (callable(marker_router) and marker_router(message)))
    return {
        "trusted": trusted,
        "candidates": candidates,
        "reason": reason or ("требуются актуальные данные" if needed else ""),
        "needed": needed,
        "freshness_satisfied": False,
        "stale": [],
        "fresh_dynamic": [],
    }


def install_runtime_latency_fixes(app=None) -> None:
    """Установить подтверждённые runtime-логами исправления Web/Telegram."""
    global _INSTALLED, _ORIGINAL_SEARCH_PLAN, _ORIGINAL_BUILD_CONTEXT, _ORIGINAL_LIVE_SEARCH

    # Для новых экземпляров и уже созданного app.
    DdgsSearch.DEFAULT_BACKENDS = SUPPORTED_DDGS_BACKENDS
    if app is not None:
        provider = getattr(getattr(app, "research", None), "search_provider", None)
        if provider is not None:
            _patch_provider(provider)

    if _INSTALLED:
        return
    _INSTALLED = True

    # Импорт внутри функции избегает циклического импорта во время загрузки app.py.
    from .app import LeshyApp

    _ORIGINAL_SEARCH_PLAN = RealtimeChatManager._search_plan
    _ORIGINAL_BUILD_CONTEXT = RealtimeChatManager._build_context
    _ORIGINAL_LIVE_SEARCH = LeshyApp.live_search

    def search_plan(self, message: str) -> dict[str, Any]:
        try:
            plan = dict(_ORIGINAL_SEARCH_PLAN(self, message))
        except AttributeError as exc:
            # Freshness расширяет штатный KnowledgeBase, но тестовые/плагинные
            # knowledge backend могут реализовывать только старый search API.
            # В таком случае деградируем к базовой политике, а не ломаем чат.
            text = str(exc)
            if "search_stale_dynamic" not in text and "search_fresh_dynamic" not in text:
                raise
            plan = _base_search_plan(self, message)
            event("freshness_backend_compat_fallback", error=text[:300])

        # Dynamic-data обычно требует live-search. Исключение: freshness-layer
        # уже нашёл в LeshyBrain релевантные сведения, проверенные в пределах TTL.
        # Тогда повторный интернет-поиск не нужен, если пользователь явно не
        # попросил перепроверить интернет прямо сейчас.
        forced_reason = adaptive_research_reason(
            message,
            list(plan.get("trusted") or []),
            list(plan.get("candidates") or []),
        )
        # Последняя защита после всех freshness/latency обёрток: старое
        # правило «новый => интернет» не применяется к генерации текста.
        if is_offline_creative_request(message):
            plan["needed"] = False
            plan["reason"] = ""
            plan["stale"] = []
            plan["fresh_dynamic"] = []
            plan["freshness_satisfied"] = False
            event("creative_local_fast_path")
            return plan
        freshness_satisfied = bool(plan.get("freshness_satisfied"))
        explicit_recheck = forced_reason == "явный запрос интернет-поиска"
        if forced_reason and (not freshness_satisfied or explicit_recheck):
            plan["needed"] = True
            plan["reason"] = forced_reason
        elif freshness_satisfied:
            plan["needed"] = False
            plan["reason"] = ""
        return plan

    def build_context(self, message, owner_id, rows, *, search_plan=None, lookup_query=None):
        messages, sources, attempted = _ORIGINAL_BUILD_CONTEXT(
            self,
            message,
            owner_id,
            rows,
            search_plan=search_plan,
            lookup_query=lookup_query,
        )
        compacted = compact_realtime_messages(messages)
        return compacted, sources, attempted

    def live_search(self, query: str, *, limit: int = 5, reason: str = "прямой запрос пользователя") -> dict:
        original = " ".join(str(query or "").split()).strip()
        grounded = ground_search_query(original)
        if grounded != original:
            event(
                "temporal_search_grounded",
                original_query=original[:500],
                grounded_query=grounded[:500],
            )
        return _ORIGINAL_LIVE_SEARCH(self, grounded, limit=limit, reason=reason)

    def llm_stream(self, messages: list[ChatMessage], cancel, owner_id: str):
        scheduler = get_inference_scheduler(self.app)
        priority = scheduler.priority_for(owner_id)
        source = str(owner_id or "system")[:160]
        try:
            with scheduler.slot(
                source=source,
                priority=priority,
                cancel_event=cancel,
                timeout=INTERACTIVE_QUEUE_TIMEOUT_SECONDS,
            ):
                streamer = getattr(self.app.llm, "stream_chat", None)
                if callable(streamer):
                    yield from streamer(messages, cancel_event=cancel)
                    return
                answer = self.app.llm.chat(messages)
                yield from self._chunk_fallback(answer)
        except TimeoutError:
            event(
                "interactive_inference_queue_timeout",
                source=source,
                priority=priority,
                timeout_seconds=INTERACTIVE_QUEUE_TIMEOUT_SECONDS,
            )
            raise

    RealtimeChatManager._search_plan = search_plan
    RealtimeChatManager._build_context = build_context
    RealtimeChatManager._llm_stream = llm_stream
    LeshyApp.live_search = live_search
    event(
        "runtime_latency_fixes_installed",
        ddgs_backends=list(SUPPORTED_DDGS_BACKENDS),
        context_char_budget=REALTIME_CONTEXT_CHAR_BUDGET,
        interactive_queue_timeout_seconds=INTERACTIVE_QUEUE_TIMEOUT_SECONDS,
    )


__all__ = [
    "INTERACTIVE_QUEUE_TIMEOUT_SECONDS",
    "REALTIME_CONTEXT_CHAR_BUDGET",
    "SUPPORTED_DDGS_BACKENDS",
    "compact_realtime_messages",
    "install_runtime_latency_fixes",
]
