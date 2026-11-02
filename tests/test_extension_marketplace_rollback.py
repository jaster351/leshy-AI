from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from leshy.extension_marketplace import ExtensionMarketplace, MarketplaceEntry
from leshy.tools_gateway import ToolManifest, ToolPermission, ToolRegistry


class ExtensionMarketplaceRollbackTests(unittest.TestCase):
    def test_failed_upgrade_restores_previous_directory_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ToolRegistry(root / "tools.json")
            extensions = root / "extensions"
            old_dir = extensions / "demo"
            old_dir.mkdir(parents=True)
            old_receipt = {"package_spec": "demo==1.0.0", "installed": True}
            (old_dir / "leshy-install.json").write_text(
                json.dumps(old_receipt, ensure_ascii=False), encoding="utf-8"
            )
            old_manifest = ToolManifest(
                tool_id="demo",
                title="Demo old",
                transport="stdio",
                command="old-python",
                args=("-m", "demo.old"),
                permissions=(ToolPermission.READ,),
                source="https://github.com/example/demo",
            )
            registry.register(old_manifest)
            marketplace = ExtensionMarketplace(registry, root=extensions)
            entry = MarketplaceEntry(
                extension_id="demo",
                title="Demo new",
                package_spec="demo==2.0.0",
                module="demo.new",
                repository="https://github.com/example/demo",
                license="MIT",
            )

            completed = SimpleNamespace(returncode=0, stdout="ok")
            with patch("leshy.extension_marketplace.subprocess.run", return_value=completed), patch(
                "leshy.extension_marketplace.McpToolsGateway.list_tools",
                side_effect=RuntimeError("health-check failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "health-check failed"):
                    marketplace.install(entry, approved=True)

            self.assertTrue(old_dir.is_dir())
            restored = json.loads((old_dir / "leshy-install.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["package_spec"], "demo==1.0.0")
            current = registry.get("demo")
            self.assertEqual(current.command, "old-python")
            self.assertEqual(current.args, ("-m", "demo.old"))
            self.assertFalse((extensions / "demo.backup").exists())
            self.assertFalse((extensions / "demo.installing").exists())


if __name__ == "__main__":
    unittest.main()
