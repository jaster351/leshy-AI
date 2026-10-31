from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from leshy.app import LeshyApp
from leshy.config import Settings


class AppRuntimeTests(unittest.TestCase):
    def make_settings(self, root: Path) -> Settings:
        brain = root / "LeshyBrain"
        return Settings(
            install_root=root,
            brain_dir=brain,
            data_dir=brain / "data",
            database_path=brain / "data" / "leshy.sqlite3",
            native_models_dir=brain / "models",
            workspace_root=brain / "workspace",
            llm_backend="echo",
            embeddings_enabled=False,
        )

    def test_chat_persists_history(self):
        with tempfile.TemporaryDirectory() as temp:
            app = LeshyApp(self.make_settings(Path(temp)))
            result = app.chat("Привет", owner_id="web:one", session_id="s1")
            self.assertEqual(result["session_id"], "s1")
            self.assertEqual(result["answer"], "Эхо: Привет")
            history = app.history("web:one", "s1")
            self.assertEqual([item["role"] for item in history], ["user", "assistant"])
            self.assertEqual(history[-1]["content"], "Эхо: Привет")

    def test_session_owner_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            app = LeshyApp(self.make_settings(Path(temp)))
            app.chat("секрет", owner_id="one", session_id="shared-id")
            with self.assertRaises(PermissionError):
                app.history("two", "shared-id")

    def test_learning_switch_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self.make_settings(Path(temp))
            app = LeshyApp(settings)
            self.assertFalse(app.learning.is_enabled())
            app.set_learning(True)
            restarted = LeshyApp(settings)
            self.assertTrue(restarted.learning.is_enabled())


if __name__ == "__main__":
    unittest.main()
