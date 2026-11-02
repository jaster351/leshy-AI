from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .diagnostics import event, problem
from .llm import ChatMessage


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LeshyBrowserUseLLM:
    """Мост между локальным `LLM.chat()` Лешего и BaseChatModel Browser Use."""

    _verified_api_keys: bool = False

    def __init__(self, llm: Any, *, name: str = "leshy-local"):
        self.llm = llm
        self._name = str(name or "leshy-local")
        self.model = self._name

    @property
    def provider(self) -> str:
        return "leshy"

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return self.model

    @staticmethod
    def _role(message: Any) -> str:
        raw = str(getattr(message, "role", "") or "").lower()
        if raw in {"system", "user", "assistant"}:
            return raw
        name = type(message).__name__.lower()
        if "system" in name:
            return "system"
        if "assistant" in name or "ai" in name:
            return "assistant"
        return "user"

    @staticmethod
    def _text_content(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for part in content:
                if isinstance(part, str):
                    chunks.append(part)
                    continue
                if isinstance(part, dict):
                    value = part.get("text") or part.get("content")
                    if isinstance(value, str):
                        chunks.append(value)
                    continue
                value = getattr(part, "text", None)
                if isinstance(value, str):
                    chunks.append(value)
            return "\n".join(x for x in chunks if x)
        return str(content or "")

    @staticmethod
    def _extract_json(text: str) -> Any:
        value = str(text or "").strip()
        value = _CODE_FENCE_RE.sub("", value).strip()
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            start = min([x for x in (value.find("{"), value.find("[")) if x >= 0], default=-1)
            if start < 0:
                raise
            for end in range(len(value), start, -1):
                candidate = value[start:end].strip()
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            raise

    async def ainvoke(self, messages: list[Any], output_format: type | None = None, **kwargs: Any):
        try:
            from browser_use.llm.views import ChatInvokeCompletion
        except Exception as exc:
            raise RuntimeError("Browser Use LLM protocol недоступен") from exc

        prepared = [
            ChatMessage(self._role(message), self._text_content(message))
            for message in messages
            if self._text_content(message).strip()
        ]
        if output_format is not None:
            try:
                schema = output_format.model_json_schema()
            except Exception:
                schema = {}
            instruction = (
                "Верни ТОЛЬКО один JSON-объект без Markdown и пояснений. "
                "Он обязан точно соответствовать этой JSON Schema:\n"
                + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            )
            prepared.append(ChatMessage("system", instruction))

        event("browser_use_local_llm_started", messages=len(prepared), structured=output_format is not None)
        try:
            answer = await asyncio.to_thread(self.llm.chat, prepared)
            if output_format is None:
                completion: Any = str(answer or "")
            else:
                payload = self._extract_json(str(answer or ""))
                completion = output_format.model_validate(payload)
            event("browser_use_local_llm_completed", structured=output_format is not None)
            return ChatInvokeCompletion(completion=completion, usage=None)
        except Exception as exc:
            problem("browser_use_local_llm_failed", str(exc))
            raise


__all__ = ["LeshyBrowserUseLLM"]
