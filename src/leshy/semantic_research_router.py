"""Hybrid internet decision: local LLM handles ambiguous intent.

Only verified external-current facts and explicit user instructions are
deterministic; the rest is semantic intent, not a rigid keyword list.
The classifier uses the local model and never sends private text to a provider.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import os
import re
import threading
import time
from urllib import request
from typing import Any

from .adaptive_internet_policy import (
    adaptive_research_reason,
    is_explicit_web_request,
    is_offline_creative_request,
)
from .diagnostics import event
from .cinema_research import cinema_query
from .research_policy import (
    _current_user_text,
    external_freshness_requested,
    is_casual_checkin,
    is_explicit_offline_request,
    is_internal_knowledge_request,
    is_local_dialogue_request,
    normalize_query,
)
from .realtime_chat import RealtimeChatManager

_BUSY = threading.BoundedSemaphore(1)
_DECISION_BUDGET_SECONDS = 3.5
_CACHED: OrderedDict[str, str] = OrderedDict()
_CACHE_GUARD = threading.Lock()
_MAX_CACHE = 128
_STRICT_WEB_REASONS = frozenset((
    "явный запрос интернет-поиска",
    "требуются актуальные данные",
    "свежая проверка бюллетеня безопасности",
    "свежие данные по безопасности",
    "события требуют актуального источника",
    "проверка ссылки или предыдущего вопроса пользователя",
))
_ANSWER_TOKEN = re.compile(r"^[\"' \t]*(WEB|LOCAL|ASK)[\"' .!\t\r\n]*$", re.I)
_GUIDANCE = (
    "Ты локальный классификатор необходимости ИНТЕРНЕТ-ПОИСКА для Лешего. "
    "Ответь ровно одним словом: WEB, LOCAL или ASK. "
    "WEB: запрос о меняющемся ВНЕШНЕМ мире и нужна свежая проверка, "
    "либо пользователь прямо просит найти информацию в интернете. "
    "LOCAL: разговор, шутка, эмоция, ругань, объяснение общих принципов, "
    "чтение собственных файлов, память, Knowledge, Конституция Федерации Леших, "
    "работа на локальном компьютере. Незнакомая тема сама по себе не требует WEB. "
    "ASK: действительно неясный объект или формулировка, нужно уточнение. "
    "Не считай цитату или отрицание требованием искать в интернете. "
    "Не генерируй сам ответ пользователю и не объясняй выбор. "
    "Если достаточно общих знаний или локального контекста, выбирай LOCAL."
)


def _resident_model(app: Any) -> bool:
    llm = getattr(app, "llm", None)
    providers = [llm]
    if hasattr(llm, "_states"):
        providers = [getattr(state, "provider", None) for state in llm._states]
    for provider in providers:
        if provider is None:
            continue
        if getattr(provider, "_model", None) is not None:
            return True
        proc = getattr(provider, "_process", None)
        try:
            if proc is not None and proc.poll() is None:
                return True
        except (OSError, ValueError):
            continue
    return False


def _short_local_inference(app: Any, query: str) -> str:
    """A bounded, small-token judgement, never a full second chat reply."""
    llm = getattr(app, "llm", None)
    providers = [llm]
    if hasattr(llm, "_states"):
        providers = [getattr(state, "provider", None) for state in llm._states]
    for provider in providers:
        if provider is None:
            continue
        proc = getattr(provider, "_process", None)
        if proc is not None and getattr(provider, "_port", None):
            try:
                if proc.poll() is not None:
                    continue
                guard = getattr(provider, "_chat_lock", None)
                if guard is None or not guard.acquire(blocking=False):
                    continue
                try:
                    from .accelerator_runtime import _open_local_llama, _visible_answer_options
                    payload = {
                        "messages": [
                            {"role": "system", "content": _GUIDANCE},
                            {"role": "user", "content": "Запрос: " + query[:600] + "\nРешение:"},
                        ],
                        "stream": False, "max_tokens": 24, "temperature": 0,
                        **_visible_answer_options(str(getattr(provider, "model", ""))),
                    }
                    req = request.Request(
                        f"http://127.0.0.1:{int(provider._port)}/v1/chat/completions",
                        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with _open_local_llama(req, timeout=2.5) as response:
                        body = json.loads(response.read().decode("utf-8"))
                    return str(body["choices"][0]["message"].get("content") or "").strip()
                finally:
                    provider._idle_last_used = time.monotonic()
                    guard.release()
            except (OSError, TimeoutError, ValueError, TypeError, KeyError, IndexError):
                continue
        model = getattr(provider, "_model", None)
        if model is None:
            continue
        guard = getattr(provider, "_lock", None)
        if guard is None or not guard.acquire(blocking=False):
            continue
        try:
            response = model.create_chat_completion(
                messages=[
                    {"role": "system", "content": _GUIDANCE},
                    {"role": "user", "content": "Запрос: " + query[:600] + "\nРешение:"},
                ],
                temperature=0,
                max_tokens=24,
                stop=["\n"],
            )
            return str(response["choices"][0]["message"].get("content") or "").strip()
        except (AttributeError, KeyError, IndexError, OSError, ValueError, TypeError):
            continue
        finally:
            provider._idle_last_used = time.monotonic()
            guard.release()
    return ""


def _classify_locally(app: Any, query: str) -> str:
    """Return LOCAL if a busy/sleeping or slow model cannot decide promptly."""
    if os.getenv("LESHY_SEMANTIC_WEB_ROUTER", "on").strip().lower() in {"0", "off", "false"}:
        return ""
    if not _resident_model(app) or not _BUSY.acquire(blocking=False):
        return ""
    key = normalize_query(query)
    with _CACHE_GUARD:
        if key in _CACHED:
            value = _CACHED[key]
            _CACHED.move_to_end(key)
            _BUSY.release()
            return value
    done = threading.Event()
    result: list[str] = []

    def worker() -> None:
        try:
            reply = _short_local_inference(app, query)
            match = _ANSWER_TOKEN.fullmatch(reply)
            if match:
                result.append(match.group(1).upper())
        except Exception:
            pass
        finally:
            done.set()
            _BUSY.release()

    thread = threading.Thread(
        target=worker, name="leshy-web-semantic-decider", daemon=True,
    )
    thread.start()
    done.wait(_DECISION_BUDGET_SECONDS)
    if not done.is_set() or not result:
        return ""
    with _CACHE_GUARD:
        _CACHED[key] = result[0]
        _CACHED.move_to_end(key)
        while len(_CACHED) > _MAX_CACHE:
            _CACHED.popitem(last=False)
    return result[0]


def choose_research(
    app: Any,
    query: str,
    trusted: list[dict] | None = None,
    candidates: list[dict] | None = None,
    *,
    initial_reason: str | None = None,
    force_search: bool = False,
) -> tuple[bool, str]:
    """Decide from intent, data source, freshness and model judgement.

    The LLM is advisory. It cannot override offline consent, trigger tools
    by itself, impersonate retrieved sources, or declare web success.
    """
    current = _current_user_text(query).strip()
    trusted = list(trusted or [])
    candidates = list(candidates or [])
    if not current:
        return False, ""
    if (is_explicit_offline_request(current)
            or is_casual_checkin(current)
            or is_local_dialogue_request(current)
            or is_internal_knowledge_request(current)
            or is_offline_creative_request(current)):
        return False, "разговор или локальные данные"
    if force_search or is_explicit_web_request(current):
        return True, "явный запрос интернет-поиска"
    # Personal updates are conversational facts, even if they contain "today".
    if (re.match(r"(?i)^(?:я|мы|мне|у меня|нам|у нас)\b", current)
            and "?" not in current
            and not re.search(r"(?i)\b(?:найди|поищи|узнай|проверь|расскажи|объясни)\b", current)):
        return False, "сообщение о личном опыте"
    if cinema_query(current):
        return True, "внешний кино-каталог"
    # An external live fact needs a source regardless of the model's memory.
    old = initial_reason
    if old is None:
        old = adaptive_research_reason(current, trusted, candidates)
    if old in _STRICT_WEB_REASONS:
        return True, str(old)
    # With fresh local knowledge answer immediately. An instruction to "find",
    # "check" or "research" without an explicit location is semantically
    # ambiguous: the model should choose whether it refers to local or web.
    ambiguous_action = bool(re.match(
        r"(?i)^(?:леший[,!:]?\s+)?(?:найди|поищи|проверь|узнай|исследуй|сравни)\b",
        current,
    ))
    if not old and not ambiguous_action:
        return False, "достаточно локальных знаний"
    # A factual knowledge gap is NOT automatically permission to search.
    # A local model may judge that the subject actually needs external data.
    if len(current) < 20:
        return False, "короткая реплика без внешнего запроса"
    model_choice = _classify_locally(app, current)
    if model_choice == "WEB":
        return True, "модель определила необходимость внешних данных"
    if model_choice == "ASK":
        return False, "модель рекомендует уточнить предмет вопроса"
    if model_choice == "LOCAL":
        return False, "смысловая оценка модели: локальный ответ"
    # No model ready or available: don't force a broken search for generic facts.
    return False, "локальная генерация с обозначением неопределённости"


def install_semantic_research_router() -> None:
    previous = RealtimeChatManager._search_plan
    if getattr(previous, "_leshy_semantic_router", False):
        return

    def search_plan(self: RealtimeChatManager, message: str) -> dict:
        plan = dict(previous(self, message))
        needed, reason = choose_research(
            self.app,
            message,
            list(plan.get("trusted") or []),
            list(plan.get("candidates") or []),
            initial_reason=str(plan.get("reason") or ""),
        )
        # A fresh trusted dynamic cache can override an outdated suggestion,
        # except when the user explicitly requests live web verification.
        if (plan.get("freshness_satisfied") and not is_explicit_web_request(message)
                and not external_freshness_requested(message)):
            needed, reason = False, "свежая локальная запись"
        plan["needed"] = needed
        plan["reason"] = reason
        if not needed:
            plan["stale"] = []
        event("semantic_research_route", online=needed, decision=reason[:100])
        return plan

    search_plan._leshy_semantic_router = True
    RealtimeChatManager._search_plan = search_plan


__all__ = ["choose_research", "install_semantic_research_router"]
