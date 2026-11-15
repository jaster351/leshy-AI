"""Regression tests for owner-scoped Parser for Plex token recovery."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from leshy.secret_store import load_secret, save_secret
from leshy.torrent_parser_bridge import TorrentParserBridge


class ParserPairingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = TorrentParserBridge(Path(self.temp.name), persistent_root=Path(self.temp.name) / "parser-state")
        self.bridge.settings.parent.mkdir(parents=True, exist_ok=True)
        self.bridge.settings.write_text(
            json.dumps({"configured": True,
                        "owner_id": "owner-test",
                        "address": "http://192.168.0.19:8766"}),
            encoding="utf-8",
        )

    def test_valid_key_is_reused_without_new_pairing(self):
        save_secret(self.bridge.secret, "valid-key" * 5)
        with patch.object(self.bridge, "_request", return_value={"authorized": True}) as request:
            result = self.bridge.pair("owner-test")
        self.assertTrue(result["paired"])
        self.assertEqual(result["auth_verified"], True)
        self.assertTrue(all("/v1/auth/check" in call.args[0] for call in request.call_args_list))

    def test_invalid_key_is_replaced_only_after_nas_rejects_it(self):
        save_secret(self.bridge.secret, "expired-key" * 5)
        fresh = "fresh-key" * 6
        calls = []
        def remote(url, body=None, secret=None, timeout=4):
            calls.append((url, secret))
            if url.endswith("/v1/auth/check"):
                if secret != fresh:
                    raise PermissionError("invalid")
                return {"authorized": True}
            if url.endswith("/v1/pair"):
                return {"token": fresh}
            raise AssertionError(url)
        with patch.object(self.bridge, "_request", side_effect=remote):
            result = self.bridge.pair("owner-test")
        self.assertTrue(result["paired"])
        self.assertEqual(load_secret(self.bridge.secret), fresh)
        self.assertEqual(sum(url.endswith("/v1/pair") for url, _ in calls), 1)

    def test_network_problem_never_discards_a_saved_key(self):
        original = "safe-local-token" * 4
        save_secret(self.bridge.secret, original)
        with patch.object(self.bridge, "_request", side_effect=ConnectionError("NAS offline")):
            with self.assertRaises(ConnectionError):
                self.bridge.pair("owner-test")
        self.assertEqual(load_secret(self.bridge.secret), original)

    def test_unverified_requester_cannot_repair(self):
        save_secret(self.bridge.secret, "safe-local-token" * 4)
        with patch.object(self.bridge, "_request") as remote:
            with self.assertRaises(PermissionError):
                self.bridge.pair("telegram:untrusted")
            remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
