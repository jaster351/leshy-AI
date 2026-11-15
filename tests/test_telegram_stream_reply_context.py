"""Telegram streaming keeps Reply context while safeguarding Parser for Plex actions."""
from __future__ import annotations

import unittest

from leshy.telegram_fast import _dialogue_text_with_reply
from leshy.torrent_parser_chat import is_explicit_parser_request


class TelegramStreamReplyTests(unittest.TestCase):
    def test_dialogue_can_refer_to_a_selected_movie_list(self):
        message = {
            "text": "Мне понравился второй фильм. Расскажи подробнее.",
            "reply_to_message": {
                "message_id": 42,
                "from": {"id": 999, "first_name": "Леший", "is_bot": True},
                "text": "1. Фильм А.\n2. Фильм Б.\n3. Фильм В.",
            },
        }
        result = _dialogue_text_with_reply(message, message["text"])
        self.assertIn("Контекст Telegram Reply", result)
        self.assertIn("ID выбранного сообщения: 42", result)
        self.assertIn("2. Фильм Б.", result)
        self.assertTrue(result.endswith(message["text"]))

    def test_text_without_reply_is_not_rewritten(self):
        original = "Подбери фильмы на вечер"
        self.assertEqual(_dialogue_text_with_reply({"text": original}, original), original)

    def test_explicit_download_tool_command_uses_only_users_words(self):
        original = "Скачай фильм Аватар"
        message = {
            "text": original,
            "reply_to_message": {"message_id": 2, "text": "Скачай фильм совершенно другой"},
        }
        self.assertTrue(is_explicit_parser_request(original))
        self.assertEqual(_dialogue_text_with_reply(message, original), original)

    def test_quoted_command_is_not_tool_authorization(self):
        message = {
            "text": "А как Вам этот вариант?",
            "reply_to_message": {
                "message_id": 3,
                "text": "Скачай фильм Выдуманное название",
            },
        }
        result = _dialogue_text_with_reply(message, message["text"])
        self.assertIn("Скачай фильм Выдуманное название", result)
        self.assertFalse(is_explicit_parser_request(result))

    def test_explicit_quote_overrides_ambiguous_recent_history(self):
        message = {
            "text": "Расскажи про этот",
            "quote": {"text": "2. Второй фильм"},
            "reply_to_message": {"message_id": 19, "text": "1. Первый фильм\n2. Второй фильм"},
        }
        result = _dialogue_text_with_reply(message, message["text"])
        self.assertIn("Выделенная пользователем цитата:", result)
        self.assertIn("2. Второй фильм", result)


if __name__ == "__main__":
    unittest.main()
