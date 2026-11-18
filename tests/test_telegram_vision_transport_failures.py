"""Регрессия: ошибки защищённого Telegram-транспорта не раскрывают bot token.

Тесты не обращаются к сети; не запускать локально без разрешения владельца.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from leshy.telegram_vision import LocalTelegramVision, TelegramImageError


class TelegramVisionTransportFailureTests(unittest.TestCase):
    def test_redirect_rejection_is_safe_image_error(self):
        bridge = Mock()
        bridge._api.return_value = {"file_path": "photos/photo.jpg", "file_size": 6}
        bridge._token.return_value = "sensitive-token"
        bridge.proxy.open.side_effect = ConnectionError(
            "Telegram перенаправил запрос; переход запрещён"
        )

        with self.assertRaises(TelegramImageError) as raised:
            LocalTelegramVision(bridge)._download({"file_id": "photo", "file_size": 6})

        self.assertIn("Не удалось скачать фотографию", str(raised.exception))
        self.assertNotIn("sensitive-token", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        bridge.proxy.open.assert_called_once()

    def test_invalid_telegram_transport_target_is_safe_image_error(self):
        bridge = Mock()
        bridge._api.return_value = {"file_path": "photos/photo.jpg", "file_size": 6}
        bridge._token.return_value = "sensitive-token"
        bridge.proxy.open.side_effect = ValueError("Некорректный адрес Telegram API")

        with self.assertRaises(TelegramImageError) as raised:
            LocalTelegramVision(bridge)._download({"file_id": "photo", "file_size": 6})

        self.assertNotIn("sensitive-token", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
