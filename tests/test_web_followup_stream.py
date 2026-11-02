from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebFollowupStreamRegressionTests(unittest.TestCase):
    def test_reference_multitask_owns_followup_lifecycle(self) -> None:
        source = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
        self.assertIn("const streams=new Map();", source)
        self.assertIn("runStream(text,requested);", source)
        self.assertIn("streams.delete(stream.sessionId||stream.tempKey)", source)
        self.assertIn("await refreshActiveView(stream)", source)

    def test_terminal_sse_releases_stream_without_waiting_for_physical_eof(self) -> None:
        source = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
        self.assertIn("let buffer='',done=null,terminal=false", source)
        self.assertIn("}else if(type==='done'){", source)
        self.assertIn("}else if(type==='duplicate'){", source)
        self.assertIn("terminal=true", source)
        self.assertIn("reader.cancel('leshy logical stream terminal')", source)

    def test_sse_callback_errors_are_not_silently_swallowed(self) -> None:
        source = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
        self.assertIn("try{data=JSON.parse(raw)}catch(_e){continue}onEvent(type,data)", source)
        self.assertNotIn("try{onEvent(type,JSON.parse(raw))}catch(_e){}", source)

    def test_extra_chat_controllers_are_not_injected_into_runtime_page(self) -> None:
        source = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
        runtime = source[source.index("def _runtime_html"):source.index("def _shared_chat_html")]
        self.assertIn("shell._runtime_html(settings)", runtime)
        self.assertIn('sidebar-session-actions.js', runtime)
        self.assertNotIn("chat-controller-canonical.js", runtime)
        self.assertNotIn("chat-idempotency.js", runtime)
        self.assertNotIn("chat-stall-watchdog.js", runtime)
        self.assertNotIn("chat-followup-finalizer.js", runtime)

    def test_request_id_contains_utc_millisecond_timestamp_and_collision_suffix(self) -> None:
        source = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
        self.assertIn("function nextRequestId()", source)
        self.assertIn("Date.now()", source)
        self.assertIn("toISOString().replace(/\\D/g,'').slice(0,17)", source)
        self.assertIn("requestSequence", source)
        self.assertIn("crypto.getRandomValues", source)
        self.assertIn("const requestId=nextRequestId()", source)
        self.assertIn("request_id:requestId", source)
        self.assertIn("session_id:requestedSession||''", source)


if __name__ == "__main__":
    unittest.main()
