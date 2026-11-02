from __future__ import annotations

import asyncio
import unittest

from pydantic import BaseModel

from leshy.browser_use_llm import LeshyBrowserUseLLM
from leshy.llm import ChatMessage


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.seen = []

    def chat(self, messages: list[ChatMessage]) -> str:
        self.seen = list(messages)
        return self.response


class _Payload(BaseModel):
    value: int


class BrowserUseLocalLLMTests(unittest.TestCase):
    def test_matches_browser_use_runtime_protocol(self) -> None:
        from browser_use.llm.base import BaseChatModel

        adapter = LeshyBrowserUseLLM(_FakeLLM("ok"))
        self.assertIsInstance(adapter, BaseChatModel)
        self.assertEqual(adapter.provider, "leshy")
        self.assertEqual(adapter.model, "leshy-local")

    def test_plain_response_uses_native_leshy_chat(self) -> None:
        from browser_use.llm.messages import UserMessage

        fake = _FakeLLM("готово")
        adapter = LeshyBrowserUseLLM(fake)
        result = asyncio.run(adapter.ainvoke([UserMessage(content="Привет")]))
        self.assertEqual(result.completion, "готово")
        self.assertTrue(fake.seen)
        self.assertEqual(fake.seen[-1].role, "user")

    def test_structured_response_is_validated(self) -> None:
        from browser_use.llm.messages import UserMessage

        fake = _FakeLLM('{"value":42}')
        adapter = LeshyBrowserUseLLM(fake)
        result = asyncio.run(adapter.ainvoke([UserMessage(content="Верни число")], output_format=_Payload))
        self.assertEqual(result.completion.value, 42)
        self.assertEqual(fake.seen[-1].role, "system")
        self.assertIn("JSON Schema", fake.seen[-1].content)


if __name__ == "__main__":
    unittest.main()
