from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote

from leshy.knowledge_repository_sync import KnowledgeRepositorySync, _blob_sha


class FakeSync(KnowledgeRepositorySync):
    def __init__(self, *args):
        super().__init__(*args)
        self.payloads = {"README.md": b"catalogue", "constitution/rules.md": b"draft rules", "updates/day/facts.jsonl": b"{}\n"}
        self.commit = "a" * 40
        self.source_tree = "b" * 40
        self.tree = "c" * 40
        self.calls = []
        self.offline = False
        self.truncated = False
        self.entries_override = None
        self.corrupt = set()
        self.cancel_on_download = False

    def _read(self, url, limit):
        self.calls.append(url)
        if self.offline:
            raise OSError("offline")
        if "/branches/" in url:
            data = {"commit": {"sha": self.commit, "commit": {"tree": {"sha": self.source_tree}}}}
        elif url.endswith(f"/git/trees/{self.source_tree}"):
            data = {"sha": self.source_tree, "tree": [{"path": "knowledge", "type": "tree", "sha": self.tree}], "truncated": False}
        elif url.endswith(f"/git/trees/{self.tree}?recursive=1"):
            entries = [{"path": name, "mode": "100644", "type": "blob", "size": len(payload), "sha": _blob_sha(payload)} for name, payload in self.payloads.items()]
            data = {"sha": self.tree, "tree": self.entries_override if self.entries_override is not None else entries, "truncated": self.truncated}
        else:
            name = unquote(url.split("/knowledge/", 1)[1])
            if self.cancel_on_download:
                self._stop.set()
            return b"corrupt" if name in self.corrupt else self.payloads[name]
        return json.dumps(data).encode()


class KnowledgeRepositorySyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundled" / "knowledge"
        self.bundle.mkdir(parents=True)
        self.sync = FakeSync(self.root / "brain" / "repository_knowledge", self.bundle)

    def tearDown(self):
        self.temporary.cleanup()

    def test_downloads_entire_folder_including_documents_and_binary_data(self):
        self.sync.payloads["assets/example.bin"] = bytes(range(256))
        self.assertEqual(self.sync.sync_once()["state"], "updated")
        root = self.sync.directory()
        for name, value in self.sync.payloads.items():
            self.assertEqual((root / name).read_bytes(), value)
        self.assertTrue(root.is_relative_to(self.root / "brain"))
        self.assertEqual(list(self.bundle.iterdir()), [])

    def test_unchanged_check_does_not_redownload_blobs(self):
        self.sync.sync_once()
        self.sync.calls.clear()
        result = self.sync.sync_once()
        self.assertEqual(result["state"], "unchanged")
        self.assertEqual(result["downloaded_files"], 0)
        self.assertFalse(any("raw.githubusercontent.com" in url for url in self.sync.calls))

    def test_downloaded_facts_import_into_the_existing_brain_database(self):
        from leshy.knowledge import KnowledgeBase
        from leshy.repository_knowledge import import_directory
        from leshy.storage import Storage, utcnow

        record = {
            "schema_version": 1, "id": "downloaded-example", "version": 1,
            "topic": "example", "claim": "A sourced example.", "category": "foundations",
            "data_class": "static", "checked_at": utcnow(), "review_status": "verified",
            "sources": [{"url": "https://docs.python.org/3/", "title": "Python", "source_family": "python"}],
        }
        self.sync.payloads["updates/day/facts.jsonl"] = (json.dumps(record) + "\n").encode()
        self.assertEqual(self.sync.sync_once()["state"], "updated")
        knowledge = KnowledgeBase(Storage(self.root / "brain" / "data" / "leshy.sqlite3"))
        result = import_directory(knowledge, self.sync.directory() / "updates")
        self.assertEqual(result["imported"], 1)
        with knowledge.storage.connection() as connection:
            row = connection.execute("SELECT claim,status FROM knowledge").fetchone()
        self.assertEqual(row["claim"], record["claim"])
        self.assertEqual(row["status"], "candidate")

    def test_updates_reuse_files_and_keep_previous_snapshot(self):
        self.sync.sync_once()
        old = self.sync.directory()
        self.sync.tree = "d" * 40
        self.sync.commit = "e" * 40
        del self.sync.payloads["constitution/rules.md"]
        self.sync.payloads["news/item.md"] = b"new item"
        result = self.sync.sync_once()
        self.assertEqual(result["state"], "updated")
        self.assertEqual(result["downloaded_files"], 1)
        self.assertNotEqual(self.sync.directory(), old)
        self.assertFalse((self.sync.directory() / "constitution/rules.md").exists())
        self.assertEqual((old / "constitution/rules.md").read_bytes(), b"draft rules")

    def test_corrupt_download_keeps_active_snapshot_and_pointer(self):
        self.sync.sync_once()
        old = self.sync.directory()
        pointer = (self.sync.cache_root / "current.json").read_bytes()
        self.sync.tree = "d" * 40
        self.sync.payloads["README.md"] = b"updated catalogue"
        self.sync.corrupt.add("README.md")
        self.assertEqual(self.sync.sync_once()["state"], "error")
        self.assertEqual(self.sync.directory(), old)
        self.assertEqual((self.sync.cache_root / "current.json").read_bytes(), pointer)
        self.assertEqual((old / "README.md").read_bytes(), b"catalogue")

    def test_offline_restart_restores_complete_cached_snapshot(self):
        self.sync.sync_once()
        cached = self.sync.directory()
        restarted = FakeSync(self.sync.cache_root, self.bundle)
        restarted.offline = True
        self.assertEqual(restarted.directory(), cached)
        self.assertEqual(restarted.sync_once()["state"], "error")
        self.assertEqual(restarted.directory(), cached)

    def test_partial_manifest_does_not_publish_a_partial_folder(self):
        self.sync.truncated = True
        self.assertEqual(self.sync.sync_once()["state"], "error")
        self.assertEqual(self.sync.directory(), self.bundle)
        self.assertFalse((self.sync.cache_root / "current.json").exists())

    def test_unsafe_paths_symlinks_and_case_collisions_are_rejected(self):
        valid = {"path": "README.md", "type": "blob", "mode": "100644", "sha": "a" * 40, "size": 1}
        for name in ("../escape", "/absolute", "a\\b", "a/../escape", "NUL.txt", "a:stream", "trailing."):
            with self.subTest(path=name):
                self.sync.entries_override = [{**valid, "path": name}]
                self.assertEqual(self.sync.sync_once()["state"], "error")
        self.sync.entries_override = [{**valid, "mode": "120000"}]
        self.assertEqual(self.sync.sync_once()["state"], "error")
        self.sync.entries_override = [valid, {**valid, "path": "readme.md"}]
        self.assertEqual(self.sync.sync_once()["state"], "error")
        self.assertEqual(self.sync.directory(), self.bundle)

    def test_locally_damaged_cache_is_repaired_from_verified_blobs(self):
        self.sync.sync_once()
        (self.sync.directory() / "README.md").write_bytes(b"damaged")
        result = self.sync.sync_once()
        self.assertEqual(result["state"], "updated")
        self.assertEqual(result["downloaded_files"], 1)
        self.assertEqual((self.sync.directory() / "README.md").read_bytes(), b"catalogue")

    def test_cancellation_does_not_activate_an_incomplete_download(self):
        self.sync.cancel_on_download = True
        self.assertEqual(self.sync.sync_once()["state"], "stopped")
        self.assertEqual(self.sync.directory(), self.bundle)
        self.assertFalse((self.sync.cache_root / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
