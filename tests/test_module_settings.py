from pathlib import Path
import tempfile
import unittest

from leshy.module_settings import list_module_states, module_enabled, set_module_enabled
from leshy.storage import Storage


class ModuleSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "leshy.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_discussed_modules_are_visible_and_enabled_by_default(self) -> None:
        states = {item["name"]: item for item in list_module_states(self.storage)}
        self.assertEqual(
            set(states),
            {
                "human-browser",
                "browser-agent",
                "document-intelligence",
                "computer-use",
                "tools-marketplace",
                "intelligent-memory",
                "knowledge-graph",
                "agent-orchestrator",
                "distributed-cluster",
                "parcel-tracking",
                "network-blocking",
                "support-consultant",
                "lenta",
                "superagent",
            },
        )
        self.assertTrue(all(item["enabled"] for item in states.values()))

    def test_browser_modules_depend_on_human_browser(self) -> None:
        set_module_enabled(self.storage, "human-browser", False)
        self.assertFalse(module_enabled(self.storage, "human-browser"))
        self.assertFalse(module_enabled(self.storage, "parcel-tracking"))
        self.assertFalse(module_enabled(self.storage, "browser-agent"))

        states = {item["name"]: item for item in list_module_states(self.storage)}
        for name in ("parcel-tracking", "browser-agent"):
            state = states[name]
            self.assertTrue(state["configured_enabled"])
            self.assertFalse(state["enabled"])
            self.assertEqual(state["dependencies"][0]["name"], "human-browser")
            self.assertFalse(state["dependencies"][0]["enabled"])

    def test_switch_survives_new_storage_instance(self) -> None:
        path = self.storage.path
        set_module_enabled(self.storage, "network-blocking", False)
        reopened = Storage(path)
        self.assertFalse(module_enabled(reopened, "network-blocking"))
        set_module_enabled(reopened, "network-blocking", True)
        self.assertTrue(module_enabled(self.storage, "network-blocking"))

    def test_unknown_module_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            set_module_enabled(self.storage, "unknown-module", False)


if __name__ == "__main__":
    unittest.main()
