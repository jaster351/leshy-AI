from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

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

    def test_local_inference_timeout_defaults_and_override(self):
        from leshy.app import _inference_timeout
        with patch.dict(os.environ, {"LESHY_LLM_REQUEST_TIMEOUT_SECONDS": ""}):
            self.assertEqual(_inference_timeout(70), 70.0)
        with patch.dict(os.environ, {"LESHY_LLM_REQUEST_TIMEOUT_SECONDS": "110"}):
            self.assertEqual(_inference_timeout(70), 110.0)
        with patch.dict(os.environ, {"LESHY_LLM_REQUEST_TIMEOUT_SECONDS": "999"}):
            self.assertEqual(_inference_timeout(70), 180.0)
        with patch.dict(os.environ, {"LESHY_LLM_REQUEST_TIMEOUT_SECONDS": "bad"}):
            self.assertEqual(_inference_timeout(45), 45.0)

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
            restarted = None
            try:
                self.assertFalse(app.learning.is_enabled())
                app.set_learning(True)
                restarted = LeshyApp(settings)
                self.assertTrue(restarted.learning.is_enabled())
            finally:
                # stop_background() is not merely cosmetic here: after enabling
                # self-learning both runtime instances may still be touching the
                # shared data directory. Stop them before TemporaryDirectory
                # starts removing that directory.
                if restarted is not None:
                    restarted.stop_background()
                app.stop_background()

    def test_stop_background_waits_for_learning_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            app = LeshyApp(self.make_settings(Path(temp)))
            app.start_background()
            app.stop_background()
            # Не учитывать одноимённые потоки других экземпляров в общем runner.
            workers = (
                app.self_learning._thread,
                app.self_learning.material_learning._thread,
                app.self_learning.social_watch._thread,
            )
            self.assertTrue(all(worker is not None for worker in workers))
            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertFalse(app.self_learning.status()["running"])

    def test_explicit_memory_is_persistent_and_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = self.make_settings(Path(temp))
            app = LeshyApp(settings)
            restarted = None
            try:
                saved = app.chat("/remember Любимый редактор — VS Code", owner_id="one", session_id="m1")
                self.assertIn("Запомнил", saved["answer"])
                restarted = LeshyApp(settings)
                items = restarted.memories("one")
                self.assertEqual(len(items), 1)
                self.assertIn("VS Code", items[0]["content"])
                self.assertEqual(restarted.memories("two"), [])
            finally:
                if restarted is not None:
                    restarted.stop_background()
                app.stop_background()


if __name__ == "__main__":
    unittest.main()
