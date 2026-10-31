from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.app import LeshyApp
from leshy.config import Settings


class MemoryPersistenceTests(unittest.TestCase):
    def test_explicit_memory_survives_app_restart_and_versioned_runtime_recreation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "install"
            env = {
                "LESHY_INSTALL_ROOT": str(root),
                "LESHY_BRAIN_DIR": str(root / "LeshyBrain"),
                "LESHY_LLM_BACKEND": "echo",
            }
            with patch.dict(os.environ, env, clear=False):
                first = LeshyApp(Settings.from_env())
                memory_id = first.remember("web:local", "Это знание должно сохраняться во всех версиях Лешего.")
                first.stop_background()

                second = LeshyApp(Settings.from_env())
                items = second.memories("web:local")
                second.stop_background()

            self.assertTrue(any(item["id"] == memory_id for item in items))
            self.assertTrue(
                any(item["content"] == "Это знание должно сохраняться во всех версиях Лешего." for item in items)
            )


if __name__ == "__main__":
    unittest.main()
