"""Regression coverage for Parser for Plex settings across Windows rebuilds and Web restarts."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from leshy.secret_store import save_secret
from leshy.torrent_parser_bridge import TorrentParserBridge


class ParserPersistenceTests(unittest.TestCase):
    def test_persists_between_dist_and_installer_brain_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stable = root / "local-state"
            source = TorrentParserBridge(root / "repo" / "dist" / "Leshy" / "LeshyBrain",
                                         persistent_root=stable)
            first = source.configure("http://192.168.0.19:8766/alice", "owner-uuid-123")
            self.assertTrue(first["configured"])
            save_secret(source.secret, "x" * 40)
            rebuilt = TorrentParserBridge(root / "new-build" / "LeshyBrain",
                                          persistent_root=stable)
            self.assertEqual(rebuilt.status("owner-uuid-123")["address"],
                             "http://192.168.0.19:8766")
            self.assertTrue(rebuilt.status("owner-uuid-123")["paired"])

    def test_migrates_existing_brain_state_without_deleting_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "old" / "LeshyBrain"
            config = brain / "settings" / "torrent_parser.json"
            token = brain / "secrets" / "torrent_parser_token.secret"
            config.parent.mkdir(parents=True)
            token.parent.mkdir(parents=True)
            config.write_text(json.dumps({
                "address": "http://192.168.0.19:8766",
                "owner_id": "owner-123",
                "configured": True,
            }), encoding="utf-8")
            save_secret(token, "a" * 40)
            bridge = TorrentParserBridge(brain, persistent_root=root / "persisted")
            self.assertTrue(bridge.status("owner-123")["paired"])
            self.assertTrue(config.exists())
            self.assertTrue(token.exists())
            self.assertTrue(bridge.settings.exists())
            self.assertTrue(bridge.secret.exists())

    def test_other_owner_cannot_read_address_pairing_or_replace_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = TorrentParserBridge(root / "brain", persistent_root=root / "state")
            bridge.configure("http://192.168.0.19:8766", "original-owner")
            save_secret(bridge.secret, "a" * 40)
            stranger = bridge.status("different-owner")
            self.assertFalse(stranger["configured"])
            self.assertFalse(stranger["paired"])
            self.assertTrue(stranger["profile_mismatch"])
            self.assertNotEqual(stranger["address"], "http://10.0.0.11:8766")
            with self.assertRaises(PermissionError):
                bridge.configure("http://10.0.0.11:8766", "different-owner")
            self.assertTrue(bridge.status("original-owner")["paired"])

    def test_canonical_settings_not_overwritten_by_legacy_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "canonical"
            first = TorrentParserBridge(root / "brainA", persistent_root=state)
            first.configure("http://10.0.0.9:8766", "original-owner")
            save_secret(first.secret, "b" * 40)
            other = root / "brainB"
            config = other / "settings" / "torrent_parser.json"
            token = other / "secrets" / "torrent_parser_token.secret"
            config.parent.mkdir(parents=True)
            token.parent.mkdir(parents=True)
            config.write_text(json.dumps({
                "address": "http://10.0.0.11:8766",
                "owner_id": "other-owner",
                "configured": True,
            }), encoding="utf-8")
            save_secret(token, "c" * 40)
            refreshed = TorrentParserBridge(other, persistent_root=state)
            self.assertEqual(refreshed.status("original-owner")["address"],
                             "http://10.0.0.9:8766")
            self.assertTrue(refreshed.status("original-owner")["paired"])

    def test_broken_windows_pairing_fails_visible_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = TorrentParserBridge(root / "brain", persistent_root=root / "stable")
            bridge.configure("http://10.0.0.9:8766", "owner")
            with patch("leshy.torrent_parser_bridge.load_secret",
                       side_effect=RuntimeError("DPAPI key unavailable")):
                status = bridge.status("owner")
            self.assertFalse(status["paired"])
            self.assertIn("pairing_error", status)


if __name__ == "__main__":
    unittest.main()
