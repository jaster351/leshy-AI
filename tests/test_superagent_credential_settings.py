"""Credential folder settings are isolated and never expose credential content."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from leshy.storage import Storage
from leshy.superagent_credential_settings import (
    find_credential_file, get_credential_folder, set_credential_folder,
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
            self.assertNotIn("SECRET_DO_NOT_LOG", repr(conn.execute("SELECT * FROM meta").fetchall()))
            events = conn.execute("SELECT payload_json FROM audit_log").fetchall()
        self.assertNotIn(str(self.directory), repr([row["payload_json"] for row in events]))

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
