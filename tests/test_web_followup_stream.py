from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebFollowupStreamRegressionTests(unittest.TestCase):
    def test_terminal_sse_events_release_web_stream_without_waiting_for_eof(self) -> None:
        source = (ROOT / "web" / "chat-followup-finalizer.js").read_text(encoding="utf-8")
        self.assertIn("TERMINAL_EVENTS=new Set(['done','error','duplicate'])", source)
        self.assertIn("controller.close()", source)
        self.assertIn("reader.cancel('leshy logical stream terminal')", source)
        self.assertIn("/v1/chat/stream", source)

    def test_runtime_loads_followup_finalizer_after_existing_web_guards(self) -> None:
        source = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
        idem = source.index('chat-idempotency.js')
        watchdog = source.index('chat-stall-watchdog.js')
        followup = source.index('chat-followup-finalizer.js')
        self.assertLess(idem, watchdog)
        self.assertLess(watchdog, followup)
        self.assertIn('/chat-followup-finalizer.js', source)

    def test_multitask_still_uses_new_request_id_for_each_followup(self) -> None:
        source = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
        self.assertIn("const requestId=(crypto.randomUUID?crypto.randomUUID()", source)
        self.assertIn("const requested=sid()", source)
        self.assertIn("runStream(text,requested)", source)


if __name__ == "__main__":
    unittest.main()
