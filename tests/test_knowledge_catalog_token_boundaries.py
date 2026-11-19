"""Regression coverage for catalogue false positives caused by substring matching."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from leshy.knowledge_catalog_lookup import find_catalog_matches


class CatalogTokenBoundaryTests(unittest.TestCase):
    def test_substring_is_not_a_relevant_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "sample.jsonl"
            record = {
                "schema_version": 1, "id": "drink-brand", "version": 1,
                "topic": "Добрый сок", "claim": "Производитель напитков Добрый.",
                "category": "brands", "data_class": "static",
                "checked_at": "2026-11-15T12:00:00+00:00",
                "review_status": "verified",
                "sources": [{"url": "https://example.com/source", "title": "Source",
                             "source_family": "example"}],
            }
            package.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            (root / "index.json").write_text(json.dumps({
                "records": {"drink-brand": {
                    "version": 1, "review_status": "verified",
                    "package": "knowledge/updates/sample.jsonl",
                }}
            }), encoding="utf-8")
            self.assertEqual(find_catalog_matches(root, "сокище"), [])
            self.assertEqual(len(find_catalog_matches(root, "Добрый сок")), 1)


if __name__ == "__main__":
    unittest.main()
