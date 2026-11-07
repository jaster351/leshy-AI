from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from types import MethodType
from urllib import request, error

from .diagnostics import event, exception
from .accelerator_runtime import _visible_answer_options
from .context_budget import _normalize_system_messages, apply_context_budget, estimate_tokens
from .llm import ChatMessage, LLMUnavailable
from .adaptive_backend import _hardware_kind
from .persona import with_leshy_identity

_CONTEXT_ERROR_RE = re.compile(r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)", re.IGNORECASE)
_MAX_AUTO_CONTINUATIONS = 1
_SLOW_STREAM_FAILURE_SECONDS = 30.0
_STREAM_IDLE_SECONDS = 90.0
_STREAM_MAX_SECONDS = 360.0
_AUTO_CONTINUATION_FIRST_ROUND_BUDGET_SECONDS = 25.0
_CONTINUATION_TAIL_CHARS = 2200
_TRAILING_CONNECTORS = {
    "а", "без", "в", "для", "до", "за", "и", "из", "или", "как", "к", "на",
    "но", "о", "от", "по", "при", "с", "со", "что", "чтобы",
    "a", "an", "and", "because", "for", "from", "of", "or", "the", "to", "with",
}


def _shrink_messages_for_context(messages, requested_tokens: int, available_tokens: int):
    """Устранять HTTP 400 8830/4096, включая случай одного длинного user msg."""
    if requested_tokens <= available_tokens or not messages:
        return None
    result = apply_context_budget(
        list(messages), context_size=max(512, int(available_tokens)),
        reserve_tokens=max(128, min(1024, int(available_tokens)//4)),
    )
    if result.tokens_after > result.budget or result.tokens_after >= estimate_tokens(list(messages)):
        return None
    return result.messages


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


def _abort_stream_response(response) -> None:
    """Разбудить блокирующий SSE read после отзыва запроса/истечения срока.

    HTTPResponse.close() сама по себе не всегда прерывает recv на Windows.
    Сначала shutdown сокета, затем close. Внутренний атрибут CPython может
    отсутствовать: тогда закрытие выполняется best-effort.
    """
    try:
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        response.close()
    except (OSError, ValueError, AttributeError):
        pass


def _stream_hard_limit_seconds() -> float:
    """User-configurable hard bound; unlike the idle bound, permits slow progress."""
    raw = str(os.getenv("LESHY_STREAM_MAX_SECONDS") or "").strip()
    try:
        return min(900.0, max(60.0, float(raw))) if raw else _STREAM_MAX_SECONDS
    except (ValueError, OverflowError):
        return _STREAM_MAX_SECONDS


def _arm_stream_watchdog(
    response, cancel_event: threading.Event, *,
    started_at: float, progress_at: list[float], idle_seconds: float,
    max_seconds: float,
):
    """End stalled streams; never kill a healthy stream at its socket timeout.

    progress_at is refreshed only for actual SSE model tokens (including
    reasoning). Keep-alive frames do not count as model progress.
    """
    stop = threading.Event()
    timed_out = threading.Event()
    reason = [""]
    hard_deadline = started_at + max_seconds

    def watch() -> None:
        while not stop.wait(0.10):
            if cancel_event.is_set():
                _abort_stream_response(response)
                return
            now = time.monotonic()
            if now >= hard_deadline:
                reason[0] = "max_total_duration"
            elif now - progress_at[0] >= idle_seconds:
                reason[0] = "no_model_progress"
            else:
                continue
            timed_out.set()
            _abort_stream_response(response)
            return

    threading.Thread(target=watch, name="leshy-sse-watchdog", daemon=True).start()
    return stop, timed_out, reason


def _llama_stream(self, messages, *, cancel_event: threading.Event | None = None):
    cancel_event = cancel_event or threading.Event()
    with self._chat_lock:
        # llama-server rejects system messages after user/assistant for Qwen3.5.
        base_messages = _normalize_system_messages(list(messages))
        active_messages = list(base_messages)
        generated_text = ""
        continuation_count = 0

        while True:
            round_started = time.monotonic()
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
                        **_visible_answer_options(getattr(self, "model", "")),
                    }).encode("utf-8")
                    req = request.Request(
                        base + "/v1/chat/completions",
                        data=payload,
                        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
                        method="POST",
                    )
                    # Socket timeout only guards individual I/O waits.
                    # A healthy Qwen generation can take 2–5 minutes on CPU
                    # while producing tokens every few seconds. Reset the idle
                    # timer on *real* tokens; keep an independent hard ceiling.
                    io_timeout = max(0.05, min(float(self.request_timeout), _STREAM_IDLE_SECONDS))
                    with request.urlopen(req, timeout=io_timeout) as response:
                        stream_started = time.monotonic()
                        last_progress = [stream_started]
                        watchdog_stop, watchdog_timeout, timeout_reason = _arm_stream_watchdog(
                            response, cancel_event,
                            started_at=stream_started, progress_at=last_progress,
                            idle_seconds=io_timeout,
                            max_seconds=max(io_timeout, _stream_hard_limit_seconds()),
                        )
                        try:
                            for raw_line in response:
                                if cancel_event.is_set():
                                    break
                                if watchdog_timeout.is_set():
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
                                change = choice.get("delta") or {}
                                delta = change.get("content")
                                # Progress is activity from the model, not
                                # HTTP keepalives. Count reasoning tokens too;
                                # a separate hard cap prevents endless thinking.
                                if delta or change.get("reasoning_content") or change.get("reasoning"):
                                    last_progress[0] = time.monotonic()
                                if delta:
                                    chunk = str(delta)
                                    round_chunks.append(chunk)
                                    if continuation_count == 0:
                                        yield chunk
                                reason = choice.get("finish_reason")
                                if reason is not None:
                                    finish_reason = str(reason)
                        except TimeoutError:
                            # socket timeout can win the race with watchdog;
                            # preserve already streamed text instead of losing
                            # the whole answer as an HTTP/Windows socket error.
                            timeout_reason[0] = "no_model_progress"
                            watchdog_timeout.set()
                        except Exception:
                            # watchdog закрыл socket, чтобы прервать recv;
                            # системная ошибка от закрытого сокета здесь ожидаема.
                            if not watchdog_timeout.is_set() and not cancel_event.is_set():
                                raise
                        finally:
                            watchdog_stop.set()
                        if watchdog_timeout.is_set():
                            event("accelerator_stream_deadline_exceeded", backend=self.backend,
                                  elapsed_ms=int((time.monotonic() - round_started) * 1000),
                                  reason=timeout_reason[0] or "unknown",
                                  output_chars=sum(map(len, round_chunks)))
                            if round_chunks:
                                # Пользователь уже получил часть видимого текста.
                                # Не превращаем её в ошибку/тишину из-за дедлайна.
                                if continuation_count:
                                    remaining = _strip_continuation_overlap(
                                        generated_text, "".join(round_chunks)
                                    )
                                    if remaining:
                                        yield remaining
                                yield "\n\n[Ответ сохранён частично: превышен защитный предел длительности или модель перестала генерировать. Можно продолжить запрос.]"
                                event("accelerator_stream_partial_preserved", backend=self.backend,
                                      output_chars=sum(map(len, round_chunks)))
                                return
                            raise LLMUnavailable(
                                "Модель не сформировала видимый ответ: "
                                + ("нет новых токенов" if timeout_reason[0] == "no_model_progress"
                                   else "достигнут максимальный срок генерации")
                            )
                    if cancel_event.is_set():
                        return
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
                    if cancel_event.is_set():
                        return
                    raise
                except Exception as exc:
                    if cancel_event.is_set():
                        return
                    exception("accelerator_stream_failed", exc, backend=self.backend)
                    raise LLMUnavailable(f"Ускоренный backend {self.backend} не смог выполнить потоковый запрос: {exc}") from exc

            if cancel_event.is_set():
                return
            round_text = "".join(round_chunks)
            if continuation_count == 0:
                if not round_text.strip():
                    event("accelerator_stream_empty_completion", backend=self.backend,
                          finish_reason=finish_reason or "missing",
                          continuation=continuation_count)
                    raise LLMUnavailable(
                        "Ускоренный backend завершил поток без видимого ответа"
                    )
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
            round_seconds = time.monotonic() - round_started
            if round_seconds >= _AUTO_CONTINUATION_FIRST_ROUND_BUDGET_SECONDS:
                event("accelerator_stream_auto_continue_skipped", backend=self.backend,
                      reason="first_round_slow", elapsed_ms=int(round_seconds * 1000),
                      finish_reason=finish_reason or "heuristic")
                yield "\n\n[Ответ прерван по лимиту времени генерации. Напишите «продолжай» для продолжения.]"
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
    ordered = self._ordered()
    cpu_recovery_only = False
    for index, state in enumerate(ordered):
        if cancel_event.is_set():
            return
        if cpu_recovery_only and _hardware_kind(state.name) != "cpu":
            continue
        started = time.monotonic()
        yielded = False
        # Отказ оценки контекста не означает, что backend недоступен.
        # Не переводим живой Vulkan/OpenVINO в cooldown за длинный prompt.
        try:
            budgeted = self._budget_for(state, prepared)
        except LLMUnavailable as exc:
            event("adaptive_llm_stream_context_rejected", backend=state.name)
            errors.append(f"{state.name}: {str(exc)[:180]}")
            continue
        try:
            provider_stream = getattr(state.provider, "stream_chat", None)
            if callable(provider_stream):
                for delta in provider_stream(budgeted, cancel_event=cancel_event):
                    if cancel_event.is_set():
                        break
                    if delta:
                        yielded = True
                        yield delta
            else:
                text = state.provider.chat(budgeted)
                if text:
                    yielded = True
                    yield text
            if not cancel_event.is_set() and not yielded:
                raise LLMUnavailable("Потоковый backend не вернул видимого текста")
        except Exception as exc:
            if cancel_event.is_set():
                # User aborted: not a broken backend, no cooldown.
                return
            latency_ms = int((time.monotonic() - started) * 1000)
            fatal = self._mark_failure(state, exc, latency_ms)
            exception("adaptive_llm_stream_backend_failed", exc, backend=state.name,
                      latency_ms=latency_ms, cooldown_seconds=self.cooldown_seconds,
                      quarantined=fatal, hardware_mode=self._hardware_mode(), routing=list(routing))
            errors.append(f"{state.name}: {type(exc).__name__}: {str(exc)[:240]}")
            if yielded:
                # Already streamed meaningful text. An exception here used
                # to discard the entire response in RealtimeChatManager,
                # including Telegram. Keep its partial body and end normally,
                # while recording backend failure for diagnostics.
                event("adaptive_llm_stream_partial_saved", backend=state.name,
                      latency_ms=latency_ms, reason=type(exc).__name__)
                yield "\n\n[Ответ прервался из-за ошибки модели. Сохранена полученная часть; можно продолжить ответ.]"
                return
            if latency_ms >= int(_SLOW_STREAM_FAILURE_SECONDS * 1000):
                # On November 7 a healthy CPU fallback never ran because
                # a slow GPU/NPU error aborted the entire request. Give
                # *one* available CPU backend a chance, never a costly
                # GPU -> NPU -> CPU cascade. No partial answer is mixed.
                cpu_pending = any(
                    _hardware_kind(other.name) == "cpu"
                    for other in ordered[index + 1:]
                )
                if not cpu_recovery_only and cpu_pending:
                    cpu_recovery_only = True
                    event("adaptive_llm_stream_slow_cpu_recovery", backend=state.name,
                          latency_ms=latency_ms, reason="no_visible_output")
                    continue
                event("adaptive_llm_stream_slow_fallback_skipped", backend=state.name,
                      latency_ms=latency_ms, reason="cpu_recovery_unavailable_or_exhausted")
                break
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
