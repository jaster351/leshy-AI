"""HTTP handler integration: Parser for Plex Web settings are scoped and durable."""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from leshy.torrent_parser_bridge import TorrentParserBridge
from leshy import web_server_impl15


class _Parent:
    def __init__(self):
        self.path = "/v1/torrent-parser/config"
        self.owner = "web-owner"
        self.headers = {"X-Leshy-UI": "torrent-parser",
                        "Content-Type": "application/json",
                        "Host": "127.0.0.1:8765"}
        self.address = "http://10.0.0.12:8766"
        self.response = None

    def _settings_owner(self):
        return self.owner

    def _json(self, status, payload):
        self.response = status, payload
        return self.response

    def _body(self):
        return {"address": self.address}

    def do_GET(self):
        return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self):
        return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})


class ParserWebSettingsTests(unittest.TestCase):
    def test_web_save_read_after_restart_and_isolate_other_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stable = root / "stable"
            first_bridge = TorrentParserBridge(root / "first" / "LeshyBrain",
                                               persistent_root=stable)
            with patch.object(web_server_impl15, "_PREVIOUS_MAKE_HANDLER",
                              return_value=_Parent), patch.object(
                              web_server_impl15, "TorrentParserBridge",
                              return_value=first_bridge):
                Handler = web_server_impl15.make_handler(
                    None, None, SimpleNamespace(brain_dir=root / "first" / "LeshyBrain"), None
                )
                writer = Handler()
                self.assertEqual(writer.do_POST()[0], HTTPStatus.OK)
                self.assertTrue(writer.response[1]["configured"])

            restarted_bridge = TorrentParserBridge(root / "second" / "LeshyBrain",
                                                   persistent_root=stable)
            with patch.object(web_server_impl15, "_PREVIOUS_MAKE_HANDLER",
                              return_value=_Parent), patch.object(
                              web_server_impl15, "TorrentParserBridge",
                              return_value=restarted_bridge):
                Handler = web_server_impl15.make_handler(
                    None, None, SimpleNamespace(brain_dir=root / "second" / "LeshyBrain"), None
                )
                owner = Handler()
                response = owner.do_GET()
                self.assertEqual(response[0], HTTPStatus.OK)
                self.assertEqual(response[1]["address"], "http://10.0.0.12:8766")
                stranger = Handler()
                stranger.owner = "other-user"
                other_response = stranger.do_GET()
                self.assertFalse(other_response[1]["configured"])
                self.assertTrue(other_response[1]["profile_mismatch"])
                self.assertNotEqual(other_response[1]["address"], "http://10.0.0.12:8766")
                denied = stranger.do_POST()
                self.assertEqual(denied[0], HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
