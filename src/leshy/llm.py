from __future__ import annotations

from dataclasses import dataclass
import json
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


class OllamaLLM:
    def __init__(self, base_url: str, model: str, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[ChatMessage]) -> str:
        payload = json.dumps({"model": self.model, "messages": [{"role": m.role, "content": m.content} for m in messages], "stream": False, "options": {"temperature": 0.25}}).encode("utf-8")
        req = request.Request(f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"Ollama недоступна: {exc}") from exc
        try:
            return str(body["message"]["content"]).strip()
        except (KeyError, TypeError) as exc:
            raise LLMUnavailable("Ollama вернула неожиданный ответ") from exc


class EchoLLM:
    """Детерминированная тестовая модель."""
    def chat(self, messages: list[ChatMessage]) -> str:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return f"Эхо: {last}"
