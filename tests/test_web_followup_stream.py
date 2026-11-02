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

    def test_extra_chat_wrappers_are_not_injected_into_runtime_page(self) -> None:
        source = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
        runtime = source[source.index("def _runtime_html"):source.index("def _shared_chat_html")]
        # Эталонный shell остаётся основой; поверх него разрешён только sidebar-модуль,
        # который не владеет send/stream lifecycle.
        self.assertIn("shell._runtime_html(settings)", runtime)
        self.assertIn('sidebar-session-actions.js', runtime)
        self.assertNotIn("chat-controller-canonical.js", runtime)
        self.assertNotIn("chat-idempotency.js", runtime)
        self.assertNotIn("chat-stall-watchdog.js", runtime)
        self.assertNotIn("chat-followup-finalizer.js", runtime)

    def test_reference_transport_still_uses_unique_request_id(self) -> None:
        source = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
        self.assertIn("const requestId=(crypto.randomUUID?crypto.randomUUID()", source)
        self.assertIn("request_id:requestId", source)
        self.assertIn("session_id:requestedSession||''", source)


if __name__ == "__main__":
    unittest.main()
