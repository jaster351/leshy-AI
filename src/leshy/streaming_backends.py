from __future__ import annotations

import json
import re
import threading
import time
from types import MethodType
from urllib import request, error

from .diagnostics import event, exception
from .context_budget import _normalize_system_messages
from .llm import ChatMessage, LLMUnavailable
from .persona import with_leshy_identity

_CONTEXT_ERROR_RE = re.compile(r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)", re.IGNORECASE)
_MAX_AUTO_CONTINUATIONS = 6
_CONTINUATION_TAIL_CHARS = 2200
_TRAILING_CONNECTORS = {
    "а", "без", "в", "для", "до", "за", "и", "из", "или", "как", "к", "на",
    "но", "о", "от", "по", "при", "с", "со", "что", "чтобы",
    "a", "an", "and", "because", "for", "from", "of", "or", "the", "to", "with",
}


def _shrink_messages_for_context(messages, requested_tokens: int, available_tokens: int):
    if requested_tokens <= available_tokens or len(messages) < 2:
        return None
    target_ratio = max(0.15, min(0.90, (available_tokens * 0.78) / requested_tokens))
    result = []
    last_index = len(messages) - 1
    for index, message in enumerate(messages):
        content = str(message.content)
        if index == last_index:
            result.append(message)
            continue
        keep = max(128, int(len(content) * target_ratio))
        if len(content) <= keep:
            result.append(message)
            continue
        clipped = content[:keep] if message.role == "system" else content[-keep:]
        try:
            result.append(type(message)(role=message.role, content=clipped))
        except TypeError:
            result.append(ChatMessage(message.role, clipped))
    return result


def _looks_truncated_text(text: str) -> bool:
    value = str(text or "").rstrip()
    if len(value) < 160:
        return False
    if value.count("```") % 2:
        return True
    if value.endswith((",", ":", "(", "[", "{", "/", "—", "-")):
        return True
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", value.lower())
    return bool(words and words[-1] in _TRAILING_CONNECTORS)


def _strip_continuation_overlap(previous: str, continuation: str) -> str:
    left = str(previous or "")
    right = str(continuation or "")
    max_overlap = min(600, len(left), len(right))
    for size in range(max_overlap, 19, -1):
        if left[-size:] == right[:size]:
            return right[size:]
    return right


def _continuation_messages(messages, generated_text: str) -> list[ChatMessage]:
    prepared = list(messages)
    tail = str(generated_text or "")[-_CONTINUATION_TAIL_CHARS:]
    if tail:
        prepared.append(ChatMessage("assistant", tail))
    prepared.append(ChatMessage(
        "user",
        "Продолжи предыдущий ответ ровно с места обрыва. Не повторяй уже написанное, "
        "не начинай ответ заново и не добавляй вступление. Сразу продолжай незавершённую "
        "фразу, список или код и доведи ответ до логического конца.",
    ))
    return prepared


def _llama_stream(self, messages, *, cancel_event: threading.Event | None = None):
    cancel_event = cancel_event or threading.Event()
    with self._chat_lock:
        # llama-server rejects system messages after user/assistant for Qwen3.5.
        base_messages = _normalize_system_messages(list(messages))
        active_messages = list(base_messages)
        generated_text = ""
        continuation_count = 0

        while True:
            context_retry = False
            finish_reason: str | None = None
            round_chunks: list[str] = []
            while True:
                try:
                    base = self._ensure_server()
                    payload = json.dumps({
                        "messages": [{"role": m.role, "content": m.content} for m in active_messages],
                        "temperature": 0.25,
                        "top_p": 0.92,
                        "max_tokens": self.max_tokens,
                        "stream": True,
                    }).encode("utf-8")
                    req = request.Request(
                        base + "/v1/chat/completions",
                        data=payload,
                        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                        method="POST",
                    )
                    with request.urlopen(req, timeout=self.request_timeout) as response:
                        for raw_line in response:
                            if cancel_event.is_set():
                                break
                            line = raw_line.decode("utf-8", errors="replace").strip()
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if line == "[DONE]":
                                break
                            try:
                                body = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            choices = body.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0]
                            delta = (choice.get("delta") or {}).get("content")
                            if delta:
                                chunk = str(delta)
                                round_chunks.append(chunk)
                                if continuation_count == 0:
                                    yield chunk
                            reason = choice.get("finish_reason")
                            if reason is not None:
                                finish_reason = str(reason)
                    break
                except error.HTTPError as exc:
                    detail = ""
                    try:
                        detail = exc.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    match = _CONTEXT_ERROR_RE.search(detail)
                    if exc.code == 400 and match and not context_retry:
                        requested_tokens, available_tokens = map(int, match.groups())
                        reduced = _shrink_messages_for_context(active_messages, requested_tokens, available_tokens)
                        if reduced is not None:
                            event("accelerator_stream_context_reduced", backend=self.backend,
                                  requested_tokens=requested_tokens, available_tokens=available_tokens,
                                  messages=len(active_messages))
                            active_messages = reduced
                            context_retry = True
                            continue
                    if exc.code >= 500:
                        self._stop_process()
                    exception("accelerator_stream_http_failed", exc, backend=self.backend, status=exc.code)
                    raise LLMUnavailable(f"Ускоренный backend {self.backend} вернул HTTP {exc.code}") from exc
                except LLMUnavailable:
                    raise
                except Exception as exc:
                    exception("accelerator_stream_failed", exc, backend=self.backend)
                    raise LLMUnavailable(f"Ускоренный backend {self.backend} не смог выполнить потоковый запрос: {exc}") from exc

            if cancel_event.is_set():
                return
            round_text = "".join(round_chunks)
            if continuation_count == 0:
                generated_text += round_text
                base_messages = list(active_messages)
            else:
                cleaned = _strip_continuation_overlap(generated_text, round_text)
                if not cleaned.strip():
                    event("accelerator_stream_auto_continue_stopped", backend=self.backend,
                          reason="empty_or_duplicate_continuation", continuation=continuation_count)
                    return
                generated_text += cleaned
                yield cleaned

            needs_more = finish_reason == "length" or _looks_truncated_text(generated_text)
            if not needs_more:
                return
            if continuation_count >= _MAX_AUTO_CONTINUATIONS:
                event("accelerator_stream_auto_continue_limit", backend=self.backend,
                      finish_reason=finish_reason or "heuristic", continuations=continuation_count,
                      generated_chars=len(generated_text))
                yield "\n\n[Ответ достиг защитного лимита автопродолжения. Напишите «продолжай», если нужно ещё.]"
                return
            continuation_count += 1
            event("accelerator_stream_auto_continue", backend=self.backend,
                  finish_reason=finish_reason or "heuristic", continuation=continuation_count,
                  generated_chars=len(generated_text))
            active_messages = _continuation_messages(base_messages, generated_text)


def _ollama_stream(self, messages, *, cancel_event: threading.Event | None = None):
    cancel_event = cancel_event or threading.Event()
    prepared = with_leshy_identity(messages)
    payload = json.dumps({"model": self.model, "messages": [{"role": m.role, "content": m.content} for m in prepared],
                          "stream": True, "options": {"temperature": 0.25}}).encode("utf-8")
    req = request.Request(f"{self.base_url}/api/chat", data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=self.timeout) as response:
            for raw_line in response:
                if cancel_event.is_set():
                    break
                try:
                    body = json.loads(raw_line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                message = body.get("message") or {}
                delta = message.get("content")
                if delta:
                    yield str(delta)
                if body.get("done"):
                    break
    except (error.URLError, TimeoutError) as exc:
        raise LLMUnavailable(f"Ollama недоступна: {exc}") from exc


def _adaptive_stream(self, messages, *, cancel_event: threading.Event | None = None):
    cancel_event = cancel_event or threading.Event()
    prepared = with_leshy_identity(messages)
    errors: list[str] = []
    routing = self._routing_priority()
    for state in self._ordered():
        if cancel_event.is_set():
            return
        started = time.monotonic()
        yielded = False
        try:
            provider_stream = getattr(state.provider, "stream_chat", None)
            if callable(provider_stream):
                for delta in provider_stream(prepared, cancel_event=cancel_event):
                    yielded = True
                    yield delta
                    if cancel_event.is_set():
                        break
            else:
                text = state.provider.chat(prepared)
                yielded = bool(text)
                if text:
                    yield text
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            fatal = self._mark_failure(state, exc, latency_ms)
            exception("adaptive_llm_stream_backend_failed", exc, backend=state.name,
                      latency_ms=latency_ms, cooldown_seconds=self.cooldown_seconds,
                      quarantined=fatal, hardware_mode=self._hardware_mode(), routing=list(routing))
            errors.append(f"{state.name}: {type(exc).__name__}: {str(exc)[:240]}")
            if yielded:
                raise
            continue
        latency_ms = int((time.monotonic() - started) * 1000)
        switched = self._mark_success(state, latency_ms)
        event("adaptive_llm_stream_backend_ok", backend=state.name, latency_ms=latency_ms,
              switched=switched, hardware_mode=self._hardware_mode(), routing=list(routing),
              stopped=bool(cancel_event.is_set()))
        return
    raise LLMUnavailable("Все выбранные локальные LLM backend недоступны: " + "; ".join(errors[-3:]))


def install_streaming_backends(llm) -> None:
    name = llm.__class__.__name__
    if name == "AdaptiveLLM":
        for state in getattr(llm, "_states", []):
            install_streaming_backends(state.provider)
        if not callable(getattr(llm, "stream_chat", None)):
            llm.stream_chat = MethodType(_adaptive_stream, llm)
        return
    if name == "LlamaServerLLM" and not callable(getattr(llm, "stream_chat", None)):
        llm.stream_chat = MethodType(_llama_stream, llm)
        return
    if name == "OllamaLLM" and not callable(getattr(llm, "stream_chat", None)):
        llm.stream_chat = MethodType(_ollama_stream, llm)


__all__ = ["install_streaming_backends"]
