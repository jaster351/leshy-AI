"""Credential folder settings are isolated and never expose credential content."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from leshy.storage import Storage
from leshy.superagent_credential_settings import (
    find_credential_file, get_credential_folder, set_credential_folder,
    list_credential_file_names,
)


class CredentialFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.storage = Storage(self.home / "state.sqlite")
        self.directory = self.home / "allowed"
        self.directory.mkdir()
        (self.directory / "account.txt").write_text("login=admin\npassword=SECRET_DO_NOT_LOG", encoding="utf-8")

    def test_owner_isolation_and_no_secret_in_storage(self):
        self.assertFalse(get_credential_folder(self.storage, "web:a")["configured"])
        data = set_credential_folder(self.storage, "web:a", str(self.directory))
        self.assertTrue(data["available"])
        self.assertEqual(data["scope"], "leshy_host")
        self.assertFalse(get_credential_folder(self.storage, "web:b")["configured"])
        with self.storage.connection() as conn:
            meta_values = [row["value"] for row in conn.execute("SELECT value FROM meta").fetchall()]
            self.assertIn(str(self.directory.resolve()), meta_values)
            self.assertNotIn("SECRET_DO_NOT_LOG", " ".join(meta_values))
            events = [row["payload_json"] for row in conn.execute("SELECT payload_json FROM audit_log").fetchall()]
        self.assertNotIn(str(self.directory), " ".join(events))
        self.assertNotIn("SECRET_DO_NOT_LOG", " ".join(events))

    def test_lookup_restricts_path_and_ambiguous_names(self):
        set_credential_folder(self.storage, "web:a", str(self.directory))
        self.assertEqual(find_credential_file(self.storage, "web:a", "account.txt"),
                         (self.directory / "account.txt").resolve())
        with self.assertRaises(ValueError):
            find_credential_file(self.storage, "web:a", "../account.txt")
        with self.assertRaises(PermissionError):
            find_credential_file(self.storage, "web:b", "account.txt")
        duplicate = self.directory / "nested"
        duplicate.mkdir()
        (duplicate / "account.txt").write_text("another", encoding="utf-8")
        with self.assertRaises(ValueError):
            find_credential_file(self.storage, "web:a", "account.txt")

    def test_credential_picker_exposes_only_unique_json_basenames(self):
        (self.directory / "node.json").write_text('{"password":"PRIVATE"}', encoding="utf-8")
        (self.directory / "ignore.log").write_text("SECRET", encoding="utf-8")
        nested = self.directory / "nested"
        nested.mkdir()
        (nested / "NODE.json").write_text('{"password":"OTHER"}', encoding="utf-8")
        (nested / "second.json").write_text('{"password":"PRIVATE2"}', encoding="utf-8")
        set_credential_folder(self.storage, "web:a", str(self.directory))
        selected = list_credential_file_names(self.storage, "web:a")
        self.assertEqual(selected["files"], ["second.json"])
        self.assertFalse(selected["truncated"])
        self.assertNotIn("PRIVATE", str(selected))
        self.assertFalse(list_credential_file_names(self.storage, "web:b")["available"])

    def test_nested_symlink_outside_selected_folder_is_ignored(self):
        outside = self.home / "outside"
        outside.mkdir()
        (outside / "external.txt").write_text("password=TOP_SECRET", encoding="utf-8")
        link = self.directory / "outside_link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable on this system")
        set_credential_folder(self.storage, "web:a", str(self.directory))
        with self.assertRaises(FileNotFoundError):
            find_credential_file(self.storage, "web:a", "external.txt")

    def test_replaced_root_link_is_refused(self):
        set_credential_folder(self.storage, "web:a", str(self.directory))
        replacement = self.home / "replacement"
        replacement.mkdir()
        original = self.home / "original-allowed"
        self.directory.rename(original)
        try:
            self.directory.symlink_to(replacement, target_is_directory=True)
        except (OSError, NotImplementedError):
            original.rename(self.directory)
            self.skipTest("Directory symlinks unavailable on this system")
        self.assertFalse(get_credential_folder(self.storage, "web:a")["available"])
        with self.assertRaises(PermissionError):
            find_credential_file(self.storage, "web:a", "account.txt")

    def test_missing_folder_and_clear(self):
        with self.assertRaises(ValueError):
            set_credential_folder(self.storage, "web:a", str(self.home / "not-found"))
        with self.assertRaises(ValueError):
            set_credential_folder(self.storage, "web:a", ".")
        set_credential_folder(self.storage, "web:a", str(self.directory))
        self.assertFalse(set_credential_folder(self.storage, "web:a", "")["configured"])

    def test_untrusted_owner_is_rejected(self):
        with self.assertRaises(PermissionError):
            set_credential_folder(self.storage, "web:local", str(self.directory))


if __name__ == "__main__":
    unittest.main()
