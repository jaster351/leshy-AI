"""Regressions from the Windows logs collected on 2026-11-05."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from leshy.accelerator_runtime import LlamaServerLLM
from leshy.browser_agent import BrowserAction, BrowserActionResult, BrowserRisk
from leshy.browser_use_adapter import BrowserUseAdapter
from leshy.context_budget import _normalize_system_messages
from leshy.llm import ChatMessage
from leshy.native_chat import NativeGGUFLLM
from leshy.os_control import install_os_control


class FakeApp:
    def __init__(self, home: Path):
        self.settings = SimpleNamespace(brain_dir=home)
        self.storage = SimpleNamespace()
        self.delegated = []

    def chat(self, message, *, owner_id="web:local", session_id=None):
        self.delegated.append(message)
        return {"answer": "normal chat", "session_id": session_id}


class WindowsLogRegressions(unittest.TestCase):
    def test_unknown_os_planner_action_is_delegated_to_search_pipeline(self):
        with TemporaryDirectory() as tmp:
            app = FakeApp(Path(tmp))
            controller = install_os_control(app)
            controller._plan_natural = lambda message: ("web_search", {"query": message})
            response = app.chat("открой в интернете актуальные новости", owner_id="owner")
            self.assertEqual(response["answer"], "normal chat")
            self.assertEqual(app.delegated, ["открой в интернете актуальные новости"])

    def test_strict_gguf_system_role_must_be_first(self):
        messages = [
            ChatMessage("user", "привет"),
            ChatMessage("system", "исследование"),
            ChatMessage("assistant", "здравствуйте"),
            ChatMessage("system", "память"),
            ChatMessage("user", "продолжи"),
        ]
        merged = _normalize_system_messages(messages)
        self.assertEqual([m.role for m in merged], ["system", "user", "assistant", "user"])
        self.assertEqual(merged[0].content, "исследование\n\nпамять")

    def test_native_gguf_fallback_merges_system_roles(self):
        captured = {}
        class Model:
            def create_chat_completion(self, **kwargs):
                captured.update(kwargs)
                return {"choices": [{"message": {"content": "готово"}}]}
        llm = NativeGGUFLLM(Path("unused.gguf"))
        with patch.object(llm, "_load", return_value=Model()):
            answer = llm.chat([
                ChatMessage("user", "вопрос"),
                ChatMessage("system", "память"),
                ChatMessage("user", "ещё вопрос"),
            ])
        self.assertEqual(answer, "готово")
        self.assertEqual([m["role"] for m in captured["messages"]], ["system", "user", "user"])
        self.assertIn("память", captured["messages"][0]["content"])

    def test_browser_read_falls_back_to_playwright_after_cdp_failure(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"LESHY_BRAIN_DIR": tmp}):
            adapter = BrowserUseAdapter(llm=object())
            action = BrowserAction("research", "https://example.org/", BrowserRisk.READ)
            with patch.object(adapter, "available", return_value=True), \
                 patch.object(adapter, "_run_sync", side_effect=RuntimeError("CDP became available")), \
                 patch.object(adapter, "_fallback_read", return_value=BrowserActionResult("ok", "fallback", {"result": "page"})) as fallback:
                result = adapter.execute(action)
            self.assertEqual(result.status, "ok")
            fallback.assert_called_once()

    def test_locked_login_profile_has_actionable_error_not_linux_hint(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"LESHY_BRAIN_DIR": tmp}):
            adapter = BrowserUseAdapter(llm=object())
            profile = Path(tmp) / "browser" / "profiles" / "lenta" / "alice"
            profile.mkdir(parents=True)
            (profile / "SingletonLock").touch()
            action = BrowserAction("agent_mutate", "https://lenta.com/", BrowserRisk.MUTATE, {
                "allowed_hosts": ["lenta.com"],
                "profile_namespace": "lenta",
                "profile_key": "alice",
                "_approval_verified": True,
            })
            with patch.object(adapter, "available", return_value=True), \
                 patch.object(adapter, "_run_sync", side_effect=RuntimeError("Browser exited before CDP became available")):
                result = adapter.execute(action)
            self.assertEqual(result.status, "failed")
            self.assertIn("закройте окно авторизации", result.message)
            self.assertNotIn("Xvfb", result.message)

    def test_voice_ui_preserves_draft_and_uses_longer_pauses(self):
        javascript = (Path(__file__).resolve().parents[1] / "web" / "voice-live.js").read_text(encoding="utf-8")
        self.assertIn("NORMAL_SILENCE_MS=5000", javascript)
        self.assertIn("const finalText=await transcribeSnapshot(true,generation);", javascript)
        self.assertIn("chatDictateButton", javascript)
        self.assertIn("send:state.mode==='conversation'", javascript)


if __name__ == "__main__":
    unittest.main()
