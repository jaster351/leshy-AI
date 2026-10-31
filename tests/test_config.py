from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.config import Settings, migrate_legacy_brain


class SettingsTests(unittest.TestCase):
    def test_paths_are_relative_to_configured_install_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "install"
            with patch.dict(os.environ, {"LESHY_INSTALL_ROOT": str(root)}, clear=False):
                settings = Settings.from_env()
            self.assertEqual(settings.brain_dir, root.resolve() / "LeshyBrain")
            self.assertEqual(settings.database_path, root.resolve() / "LeshyBrain" / "data" / "leshy.sqlite3")
            self.assertEqual(settings.native_models_dir, root.resolve() / "LeshyBrain" / "models")

    def test_legacy_migration_copies_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            legacy = base / "old-brain"
            target = base / "install" / "LeshyBrain"
            (legacy / "models").mkdir(parents=True)
            (legacy / "models" / "model.gguf").write_bytes(b"model-data")
            (legacy / "data.txt").write_text("knowledge", encoding="utf-8")
            self.assertTrue(migrate_legacy_brain(legacy, target))
            self.assertEqual((target / "data.txt").read_text(encoding="utf-8"), "knowledge")
            self.assertEqual((target / "models" / "model.gguf").read_bytes(), b"model-data")
            self.assertTrue((legacy / "data.txt").exists())
            self.assertFalse((base / "install" / "LeshyBrain.migrating").exists())

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            legacy = base / "old"
            target = base / "new"
            legacy.mkdir()
            target.mkdir()
            (legacy / "state").write_text("old", encoding="utf-8")
            (target / "state").write_text("current", encoding="utf-8")
            self.assertFalse(migrate_legacy_brain(legacy, target))
            self.assertEqual((target / "state").read_text(encoding="utf-8"), "current")


if __name__ == "__main__":
    unittest.main()
