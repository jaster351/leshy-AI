"""Regression tests: a missing PYZ module can load from a verified onedir fallback."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "leshy.network_blocking_module",
    "leshy.network_blocking_runtime",
    "leshy.provider_support",
    "leshy.private_support_bootstrap",
)


def load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrozenCriticalModuleFallbackTests(unittest.TestCase):
    def test_staged_source_fallbacks_pass_preflight(self):
        preflight = load_script(ROOT / "windows" / "check-frozen-bundle.py", "bundle_preflight_test")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            toc = directory / "PYZ-00.toc"
            toc.write_text("[]", encoding="utf-8")
            package = directory / "Leshy"
            fallback = package / "_internal" / "leshy"
            fallback.mkdir(parents=True)
            for name in REQUIRED:
                stem = name.split(".")[-1]
                shutil.copy2(ROOT / "src" / "leshy" / (stem + ".py"), fallback / (stem + ".py"))
            preflight.archive_preflight(toc, package)
            (fallback / "network_blocking_runtime.py").unlink()
            with self.assertRaisesRegex(RuntimeError, "network_blocking_runtime"):
                preflight.archive_preflight(toc, package)

    def test_frozen_launcher_imports_verified_source_modules(self):
        launcher = load_script(ROOT / "windows" / "launcher.py", "frozen_launcher_test")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fallback = root / "leshy"
            fallback.mkdir()
            for name in REQUIRED:
                (fallback / (name.split(".")[-1] + ".py")).write_text("EXAMPLE = True\n", encoding="utf-8")
            old_modules = {name: sys.modules.get(name) for name in REQUIRED}
            try:
                def missing(name, *args, **kwargs):
                    raise ModuleNotFoundError(f"No module named '{name}'", name=name)

                with (
                    patch.object(sys, "frozen", True, create=True),
                    patch.object(sys, "_MEIPASS", str(root), create=True),
                    patch("importlib.import_module", side_effect=missing),
                ):
                    launcher._install_frozen_critical_modules()
                    self.assertTrue(all(sys.modules[name].EXAMPLE for name in REQUIRED))
            finally:
                for name, old in old_modules.items():
                    if old is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = old

    def test_no_fallback_when_not_frozen(self):
        launcher = load_script(ROOT / "windows" / "launcher.py", "regular_launcher_test")
        with patch.object(sys, "frozen", False, create=True):
            launcher._install_frozen_critical_modules()


if __name__ == "__main__":
    unittest.main()
