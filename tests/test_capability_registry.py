from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from leshy.capability_registry import CapabilityRegistry, is_self_capability_question


class _Storage:
    @contextmanager
    def connection(self):
        with sqlite3.connect(":memory:") as conn:
            yield conn


class _App:
    storage = _Storage()

    def status(self):
        return {
            "version": "1.10.113",
            "model_ready": True,
            "learning_enabled": False,
            "telegram": {"configured": True, "running": False},
        }


class CapabilityRegistryTests(unittest.TestCase):
    def test_self_question_detection_does_not_capture_general_news(self):
        self.assertTrue(is_self_capability_question("Что ты умеешь?"))
        self.assertTrue(is_self_capability_question("Какие у тебя возможности?"))
        self.assertTrue(is_self_capability_question("Расскажи о своих возможностях"))
        self.assertFalse(is_self_capability_question("Какие новости в мире сегодня?"))

    def test_catalog_is_not_proof_of_external_network(self):
        root = Path(__file__).resolve().parents[1]
        data = CapabilityRegistry(root).snapshot(_App())
        self.assertEqual(data["source"], "catalog")
        by_id = {x["id"]: x for x in data["features"]}
        self.assertEqual(by_id["chat"]["readiness"], "ready")
        self.assertEqual(by_id["memory"]["readiness"], "ready")
        self.assertEqual(by_id["learning"]["readiness"], "disabled")
        self.assertEqual(by_id["search"]["readiness"], "not_checked")
        self.assertEqual(by_id["telegram"]["readiness"], "not_checked")
        self.assertEqual(by_id["sandbox"]["implementation"], "planned")
        self.assertFalse(by_id["search"]["e2e_verified"])

    def test_changelog_is_fallback_history_not_invented_capabilities(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "capabilities.json").write_text("{invalid", encoding="utf-8")
            (root / "CHANGELOG.md").write_text(
                "## 1.10.113 — Учёт возможностей\n\nИзменения в коде.\n",
                encoding="utf-8",
            )
            registry = CapabilityRegistry(root)
            snapshot = registry.snapshot()
            self.assertEqual(snapshot["source"], "changelog_fallback")
            self.assertEqual(snapshot["features"], [])
            self.assertEqual(len(snapshot["history_backup"]), 1)

    def test_duplicate_ids_are_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            row = {
                "id": "chat", "title": "Чат", "implementation": "implemented",
                "probe": "model_ready", "description": "test",
            }
            (root / "capabilities.json").write_text(json.dumps({
                "schema_version": 1, "features": [row, row],
            }), encoding="utf-8")
            data = CapabilityRegistry(root).snapshot()
            self.assertEqual(data["source"], "changelog_fallback")
            self.assertEqual(data["features"], [])

    def test_self_context_provides_source_and_uncertainty(self):
        registry = CapabilityRegistry(Path(__file__).resolve().parents[1])
        content = registry.context_for_question(_App())
        self.assertIn("ЛОКАЛЬНЫЙ РЕЕСТР", content)
        self.assertIn("runtime=not_checked", content)
        self.assertIn("без реального результата вызова", content)
        self.assertLessEqual(len(content), 9000)


if __name__ == "__main__":
    unittest.main()
