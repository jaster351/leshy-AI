from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from leshy.knowledge import KnowledgeBase
from leshy.repository_knowledge import import_directory
from leshy.knowledge_catalog_lookup import find_catalog_matches, load_relevant_catalog_knowledge
from leshy.storage import Storage, utcnow


class RepositoryKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "catalog"
        self.catalog.mkdir()
        self.knowledge = KnowledgeBase(Storage(self.root / "brain.sqlite3"))
        self.record = {
            "schema_version": 1, "id": "example-fact", "version": 1,
            "topic": "public fact", "claim": "A sourced public statement.",
            "category": "foundations", "data_class": "static",
            "checked_at": utcnow(), "ttl_seconds": None, "review_status": "verified",
            "sources": [{"url": "https://docs.python.org/3/", "title": "Python docs", "source_family": "python"}],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, *records, name="batch.jsonl"):
        (self.catalog / name).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8",
        )

    def rows(self):
        with self.knowledge.storage.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM knowledge ORDER BY id")]

    def test_verified_file_label_does_not_promote_and_repeat_is_idempotent(self):
        self.write(self.record)
        self.assertEqual(import_directory(self.knowledge, self.catalog)["imported"], 1)
        self.assertEqual(import_directory(self.knowledge, self.catalog)["skipped"], 1)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["status"], "candidate")
        with self.knowledge.storage.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM research_tasks").fetchone()[0], 1)

    def test_invalid_later_line_prevents_partial_file_import(self):
        invalid = copy.deepcopy(self.record)
        invalid["id"] = "invalid-fact"
        invalid["sources"][0]["url"] = "https://127.0.0.1/private"
        self.write(self.record, invalid)
        result = import_directory(self.knowledge, self.catalog)
        self.assertTrue(result["errors"])
        self.assertEqual(self.rows(), [])

    def test_revision_retires_old_fact_without_deleting_history(self):
        self.write(self.record)
        import_directory(self.knowledge, self.catalog)
        corrected = {**self.record, "version": 2, "claim": "A corrected sourced statement."}
        self.write(corrected)
        import_directory(self.knowledge, self.catalog)
        self.assertEqual([row["status"] for row in self.rows()], ["stale", "candidate"])
        self.write(self.record)
        self.assertEqual(import_directory(self.knowledge, self.catalog)["skipped"], 1)
        self.assertEqual([row["status"] for row in self.rows()], ["stale", "candidate"])

    def test_same_revision_with_changed_content_is_rejected(self):
        self.write(self.record)
        import_directory(self.knowledge, self.catalog)
        self.write({**self.record, "claim": "Silently changed text."})
        self.assertTrue(import_directory(self.knowledge, self.catalog)["errors"])
        self.assertEqual(self.rows()[0]["claim"], self.record["claim"])

    def test_retraction_only_affects_records_owned_by_this_importer(self):
        self.knowledge.add_candidate(topic=self.record["topic"], claim=self.record["claim"],
                                     source_url=self.record["sources"][0]["url"])
        self.write(self.record)
        import_directory(self.knowledge, self.catalog)
        self.write({**self.record, "version": 2, "review_status": "retracted"})
        import_directory(self.knowledge, self.catalog)
        self.assertEqual(self.rows()[0]["status"], "candidate")

    def test_retracted_owned_record_can_return_as_a_new_candidate_revision(self):
        self.write(self.record)
        import_directory(self.knowledge, self.catalog)
        self.write({**self.record, "version": 2, "review_status": "retracted"})
        import_directory(self.knowledge, self.catalog)
        self.assertEqual(self.rows()[0]["status"], "rejected")
        self.write({**self.record, "version": 3})
        import_directory(self.knowledge, self.catalog)
        self.assertEqual(self.rows()[0]["status"], "candidate")

    def test_expired_dynamic_data_is_not_refreshed_by_import(self):
        old = {**self.record, "data_class": "dynamic", "checked_at": "2020-01-01T00:00:00Z", "ttl_seconds": 60}
        self.write(old)
        import_directory(self.knowledge, self.catalog)
        self.assertEqual(self.rows()[0]["checked_at"], old["checked_at"])
        self.assertEqual(self.knowledge.search_candidates("public fact"), [])


    def _write_catalog_index(self, metadata):
        (self.catalog / "index.json").write_text(
            json.dumps({"schema_version": 1, "records": metadata}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_on_demand_lookup_is_disabled_when_learning_is_off(self):
        self.write(self.record)
        self._write_catalog_index({
            self.record["id"]: {
                "version": 1, "package": "knowledge/updates/batch.jsonl",
                "review_status": "verified",
            },
        })
        self.assertEqual(
            load_relevant_catalog_knowledge(
                self.knowledge, self.catalog, "public fact", enabled=False,
            ), [],
        )
        self.assertEqual(self.rows(), [])
        matches = load_relevant_catalog_knowledge(
            self.knowledge, self.catalog, "public fact", enabled=True,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["review_status"], "candidate")
        self.assertEqual(self.rows()[0]["status"], "candidate")
        self.assertEqual(self.rows()[0]["source_url"], "https://docs.python.org/3/")
        self.assertEqual(
            len(load_relevant_catalog_knowledge(
                self.knowledge, self.catalog, "public fact", enabled=True,
            )), 1,
        )
        self.assertEqual(len(self.rows()), 1)

    def test_lookup_uses_only_manifest_latest_revision(self):
        updated = {**self.record, "version": 2, "claim": "A revised public fact."}
        self.write(self.record, name="old.jsonl")
        self.write(updated, name="new.jsonl")
        self._write_catalog_index({
            self.record["id"]: {
                "version": 2, "package": "knowledge/updates/new.jsonl",
                "review_status": "verified",
            },
        })
        matches = find_catalog_matches(self.catalog, "public fact")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["record"]["claim"], updated["claim"])
        self.assertEqual(matches[0]["record"]["version"], 2)

    def test_lookup_excludes_retracted_and_expired_records(self):
        self.write(self.record)
        self._write_catalog_index({
            self.record["id"]: {
                "version": 2, "package": "knowledge/updates/batch.jsonl",
                "review_status": "retracted",
            },
        })
        self.assertEqual(find_catalog_matches(self.catalog, "public fact"), [])
        expired = {
            **self.record, "data_class": "dynamic",
            "checked_at": "2020-01-01T00:00:00Z", "ttl_seconds": 60,
        }
        self.write(expired)
        self._write_catalog_index({
            self.record["id"]: {
                "version": 1, "package": "knowledge/updates/batch.jsonl",
                "review_status": "verified",
            },
        })
        self.assertEqual(find_catalog_matches(self.catalog, "public fact"), [])

    def test_learning_toggle_changed_during_lookup_prevents_import(self):
        self.write(self.record)
        self._write_catalog_index({
            self.record["id"]: {
                "version": 1, "package": "knowledge/updates/batch.jsonl",
                "review_status": "verified",
            },
        })
        states = iter((True, False))
        matches = load_relevant_catalog_knowledge(
            self.knowledge, self.catalog, "public fact",
            enabled=lambda: next(states), limit=4,
        )
        self.assertEqual(matches, [])
        self.assertEqual(self.rows(), [])

    def test_lookup_does_not_match_substrings_inside_other_words(self):
        record = {**self.record, "topic": "Котировки", "claim": "Цена акций растёт."}
        self.write(record)
        self._write_catalog_index({
            record["id"]: {
                "version": 1, "package": "knowledge/updates/batch.jsonl",
                "review_status": "verified",
            },
        })
        self.assertEqual(find_catalog_matches(self.catalog, "кот"), [])
        self.assertEqual(len(find_catalog_matches(self.catalog, "котировки")), 1)

    def test_lookup_skips_corrupt_or_incomplete_index(self):
        self.write(self.record)
        index = self.catalog / "index.json"
        index.write_text('{"schema_version":1,"records":', encoding="utf-8")
        self.assertEqual(find_catalog_matches(self.catalog, "public fact"), [])
        index.write_bytes(bytes([255]))
        self.assertEqual(find_catalog_matches(self.catalog, "public fact"), [])

    def test_lookup_requires_a_current_catalog_index(self):
        self.write(self.record)
        self.assertEqual(find_catalog_matches(self.catalog, "public fact"), [])



if __name__ == "__main__":
    unittest.main()
