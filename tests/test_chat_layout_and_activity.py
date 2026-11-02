from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ChatLayoutAndActivityTests(unittest.TestCase):
    def test_layout_module_is_visual_only_and_reserves_composer_space(self) -> None:
        source = (ROOT / "web" / "chat-layout-status.js").read_text(encoding="utf-8")
        self.assertIn("--leshy-composer-inset", source)
        self.assertIn("ResizeObserver", source)
        self.assertIn("padding-bottom:calc(var(--leshy-composer-inset)", source)
        self.assertIn("scroll-padding-bottom", source)
        self.assertIn("leshy:activity", source)
        self.assertIn("Леший работает", source)
        self.assertNotIn("window.sendFrom=", source)
        self.assertNotIn("/v1/chat/stream", source)
        self.assertNotIn("request_id", source)

    def test_reference_transport_is_still_unchanged_owner(self) -> None:
        source = (ROOT / "web" / "chat-multitask.js").read_text(encoding="utf-8")
        self.assertIn("window.sendFrom=async function(input)", source)
        self.assertIn("/v1/chat/stream", source)
        self.assertIn("event('activity'", source)

    def test_runtime_injects_layout_status_without_canonical_controller(self) -> None:
        source = (ROOT / "src" / "leshy" / "web_server_impl7.py").read_text(encoding="utf-8")
        self.assertIn('/chat-layout-status.js', source)
        runtime = source[source.index("def _runtime_html"):source.index("def _shared_chat_html")]
        self.assertIn('/sidebar-session-actions.js', runtime)
        self.assertIn('/chat-layout-status.js', runtime)
        self.assertNotIn('/chat-controller-canonical.js', runtime)
        self.assertNotIn('/chat-stall-watchdog.js', runtime)


if __name__ == "__main__":
    unittest.main()
