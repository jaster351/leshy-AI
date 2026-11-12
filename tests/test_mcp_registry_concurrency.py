"""Concurrent MCP registry edits must not erase peer registrations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
import unittest

from leshy.tools_gateway import ToolManifest, ToolRegistry


class SlowRegistry(ToolRegistry):
    def _read(self):
        items = super()._read()
        time.sleep(0.004)
        return items


class ConcurrentMcpRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "registry.json"

    def test_concurrent_instances_preserve_every_registered_tool(self):
        def add(index):
            registry = SlowRegistry(self.path)
            registry.register(ToolManifest(
                tool_id=f"tool-{index}", title=f"Tool {index}",
                transport="stdio", command="python",
            ))

        with ThreadPoolExecutor(max_workers=12) as workers:
            list(workers.map(add, range(20)))
        registry = ToolRegistry(self.path)
        self.assertEqual({tool.tool_id for tool in registry.list()},
                         {f"tool-{i}" for i in range(20)})
        self.assertTrue(self.path.with_suffix(".json.lock").exists())

    def test_parallel_toggles_do_not_remove_unrelated_tools(self):
        registry = ToolRegistry(self.path)
        for index in range(10):
            registry.register(ToolManifest(
                tool_id=f"tool-{index}", title=str(index),
                transport="stdio", command="python",
            ))

        def disable(index):
            SlowRegistry(self.path).set_enabled(f"tool-{index}", False)

        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(disable, range(10)))
        self.assertEqual(len(registry.list()), 10)
        self.assertTrue(all(not item.enabled for item in registry.list()))


if __name__ == "__main__":
    unittest.main()
