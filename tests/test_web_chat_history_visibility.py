from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "web" / "chat-multitask.js"


class WebChatHistoryVisibilityRegressionTests(unittest.TestCase):
    """Статические регрессии исчезновения чата после SSE done."""

    def test_same_session_refresh_does_not_clear_before_fetch(self) -> None:
        js = SCRIPT.read_text(encoding="utf-8")
        history = js.split("async function loadHistory(id,options={}){", 1)[1].split(
            "window.loadHistory=loadHistory;", 1
        )[0]
        self.assertIn("const keepVisible=!!options.keepVisible&&sid()===id;", history)
        self.assertIn("if(messages&&!keepVisible)messages.textContent='';", history)
        self.assertIn("const r=await api('/v1/history?", history)
        self.assertIn("if(generation!==historyGeneration||sid()!==id)return;", history)
        self.assertIn("(!items.length||!hasExpectedAnswer)", history)
        self.assertIn("event('history-sync-deferred'", history)
        self.assertIn("for(const m of items)window.addMessage(m.role,m.content,[],m.created_at);", history)

    def test_failed_same_session_history_keeps_visible_answer(self) -> None:
        js = SCRIPT.read_text(encoding="utf-8")
        history = js.split("async function loadHistory(id,options={}){", 1)[1].split(
            "window.loadHistory=loadHistory;", 1
        )[0]
        self.assertIn("if(keepVisible&&messages.querySelector('.msg'))", history)
        self.assertIn("event('history-sync-failed'", history)
        self.assertIn("if(messages)messages.textContent='';", history)

    def test_sse_completion_preserves_both_messages_until_reconciled(self) -> None:
        js = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("stream.finished=true;", js)
        self.assertIn("if(stream.finished||sid()!==stream.sessionId", js)
        self.assertIn("stream.body.textContent=stream.raw;", js)
        self.assertIn("await loadHistory(stream.sessionId,{keepVisible:true,expectedAnswer:stream.raw});", js)
        self.assertIn("await refreshActiveView(stream)", js)


if __name__ == "__main__":
    unittest.main()
