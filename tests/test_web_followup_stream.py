from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebFollowupStreamRegressionTests(unittest.TestCase):
    def test_old_terminal_wrapper_remains_available_but_is_not_runtime_owner(self) -> None:
        source = (ROOT / "web" / "chat-followup-finalizer.js").read_text(encoding="utf-8")
        self.assertIn("TERMINAL_EVENTS=new Set(['done','error','duplicate'])", source)
        self.assertIn("reader.cancel('leshy logical stream terminal')", source)
        self.assertIn("/v1/chat/stream", source)

    def test_runtime_loads_canonical_controller_after_existing_web_guards(self) -> None:
        source = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
        idem = source.index('chat-idempotency.js')
        watchdog = source.index('chat-stall-watchdog.js')
        canonical = source.index('chat-controller-canonical.js')
        self.assertLess(idem, watchdog)
        self.assertLess(watchdog, canonical)
        self.assertIn("html = html.replace('<script src=\"/chat-followup-finalizer.js\"></script>\\n', \"\")", source)

    def test_canonical_controller_uses_new_request_id_and_current_session_for_each_followup(self) -> None:
        source = (ROOT / "web" / "chat-controller-canonical.js").read_text(encoding="utf-8")
        self.assertIn("requestId:rid()", source)
        self.assertIn("sessionId:sid()", source)
        self.assertIn("session_id:req.sessionId", source)
        self.assertIn("request_id:req.requestId", source)
        self.assertIn("if(active===req)active=null;", source)


if __name__ == "__main__":
    unittest.main()
