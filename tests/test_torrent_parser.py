"""Offline regression of natural-language Plex intents and Telegram owner isolation."""
from __future__ import annotations
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from leshy.torrent_parser_bridge import parse_request, parse_media_request, parse_premiere_request
from leshy.torrent_parser_chat import authorized_owner_for_parser


class NaturalRequestsTests(unittest.TestCase):
    def test_first_avatar_part(self):
        self.assertEqual(parse_media_request("Скачай первую часть Аватара."),
                         ("movie", "Аватара", "first"))

    def test_first_avatar_with_movie_keyword(self):
        self.assertEqual(parse_media_request("Скачай первую часть фильма Аватар"),
                         ("movie", "Аватар", "first"))

    def test_original_punctuation(self):
        self.assertEqual(parse_request("Скачай фильм, Обитель зла."),
                         ("movie", "Обитель зла"))

    def test_real_telegram_and_web_command_exact_lowercase(self):
        self.assertEqual(parse_media_request("Скачай фильм, обитель зла."),
                         ("movie", "обитель зла", ""))

    def test_regular_download(self):
        self.assertEqual(parse_media_request("Скачай фильм Аватар 2009"),
                         ("movie", "Аватар 2009", ""))

    def test_music(self):
        self.assertEqual(parse_request("Скачай группу Би-2"), ("music", "Би-2"))

    def test_no_unsafe_generic_command_capture(self):
        self.assertIsNone(parse_media_request("Скачай обновление Windows"))

    def test_watch_intent(self):
        self.assertTrue(parse_premiere_request("Следи за новыми фильмами в кинотеатрах"))


class ParserAccessTests(unittest.TestCase):
    def test_only_confirmed_telegram_link_can_access_web_config(self):
        bridge = Mock()
        bridge.owner_allowed.side_effect = lambda owner: owner == "confirmed-web-owner"
        identity = SimpleNamespace(
            resolve_telegram=lambda user: (
                "confirmed-web-owner" if user == "100" else "telegram:" + user
            )
        )
        app = SimpleNamespace(owner_identity=identity)
        self.assertEqual(
            authorized_owner_for_parser(app, bridge, "telegram:100"),
            "confirmed-web-owner",
        )
        self.assertEqual(
            authorized_owner_for_parser(app, bridge, "telegram:101"),
            "telegram:101",
        )

    def test_bot_admin_does_not_implicitly_borrow_other_users_nas(self):
        bridge = Mock()
        bridge.owner_allowed.side_effect = lambda owner: owner == "foreign-web-owner"
        bridge._read.return_value = {"owner_id": "foreign-web-owner"}
        bot = SimpleNamespace(_is_owner=lambda telegram_id: telegram_id == "100")
        app = SimpleNamespace(telegram=bot, owner_identity=None)
        self.assertEqual(
            authorized_owner_for_parser(app, bridge, "telegram:100"),
            "telegram:100",
        )

    def test_missing_bot_owner_never_inherits_parser_secret(self):
        bridge = Mock()
        bridge.owner_allowed.return_value = False
        app = SimpleNamespace(telegram=None)
        self.assertEqual(
            authorized_owner_for_parser(app, bridge, "telegram:100"),
            "telegram:100",
        )


if __name__ == "__main__":
    unittest.main()
