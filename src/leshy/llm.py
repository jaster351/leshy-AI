from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Protocol
from urllib import request, error


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class LLM(Protocol):
    def chat(self, messages: list[ChatMessage]) -> str: ...


class LLMUnavailable(RuntimeError):
    pass


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_ANALYSIS_FENCE_RE = re.compile(
    r"```(?:analysis|reasoning|thoughts?)\s*\n.*?```",
    re.IGNORECASE | re.DOTALL,
)
_REASONING_LEAK_MARKERS = (
    "пользователь просит",
    "пользователь хочет",
    "сначала нужно понять",
    "сначала нужно",
    "нужно уточнить",
    "важно учитывать",
    "нужно понять, что",
    "нужно объяснить причины",
    "нужно предложить альтернатив",
    "the user asks",
    "the user wants",
    "first i need to",
    "i should explain",
)
_FINAL_PARAGRAPH_STARTS = (
    "к сожалению",
    "я могу",
    "я не могу",
    "не могу",
    "могу",
    "да,",
    "нет,",
    "нашёл",
    "нашел",
    "интернет",
    "для этого",
    "если хотите",
    "если вам",
)


def finalize_model_output(text: str) -> str:
    """Возвращает только пользовательский ответ, скрывая служебное рассуждение модели.

    Некоторые локальные reasoning-модели могут вернуть reasoning отдельным
    `<think>`-блоком, markdown-блоком `analysis` или обычным русским текстом перед
    финальным ответом. Служебные рассуждения не являются частью интерфейса Лешего.
    """

    value = str(text or "").strip()
    if not value:
        return ""

    value = _THINK_BLOCK_RE.sub("", value)
    value = _ANALYSIS_FENCE_RE.sub("", value).strip()

    # Убираем открытые маркеры некоторых chat-template/runtime.
    value = re.sub(r"(?im)^\s*(?:analysis|reasoning|thoughts?)\s*:\s*", "", value).strip()

    lowered = value.lower()
    if any(marker in lowered for marker in _REASONING_LEAK_MARKERS):
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
        final_index: int | None = None
        for index, paragraph in enumerate(paragraphs):
            start = paragraph.lower().lstrip("*-—0123456789. )")
            if index > 0 and any(start.startswith(prefix) for prefix in _FINAL_PARAGRAPH_STARTS):
                final_index = index
        if final_index is not None:
            value = "\n\n".join(paragraphs[final_index:]).strip()

    return value


class OllamaLLM:
    def __init__(self, base_url: str, model: str, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[ChatMessage]) -> str:
        # Local import avoids a module cycle: persona uses ChatMessage defined above.
        from .persona import with_leshy_identity

        prepared_messages = with_leshy_identity(messages)
        payload = json.dumps({"model": self.model, "messages": [{"role": m.role, "content": m.content} for m in prepared_messages], "stream": False, "options": {"temperature": 0.25}}).encode("utf-8")
        req = request.Request(f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"Ollama недоступна: {exc}") from exc
        try:
            value = finalize_model_output(str(body["message"]["content"]))
            if not value:
                raise LLMUnavailable("Ollama вернула пустой пользовательский ответ")
            return value
        except (KeyError, TypeError) as exc:
            raise LLMUnavailable("Ollama вернула неожиданный ответ") from exc


class EchoLLM:
    """Детерминированная тестовая модель."""
    def chat(self, messages: list[ChatMessage]) -> str:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return f"Эхо: {last}"
