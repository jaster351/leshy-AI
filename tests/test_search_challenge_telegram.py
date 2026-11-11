from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace

from leshy.search_challenge_telegram import send_challenge_photo


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({"ok": True, "result": {"message_id": 12}}).encode()


class FakeProxy:
    def __init__(self):
        self.requests = []

    def open(self, req, *, timeout):
        self.requests.append((req, timeout))
        return FakeResponse()


class ChallengePhotoTests(unittest.TestCase):
    def setUp(self):
        self.proxy = FakeProxy()
        self.telegram = SimpleNamespace(proxy=self.proxy, _token=lambda: "test-token-telegram")
        self.photo = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimg").decode()

    def test_photo_is_uploaded_using_telegram_transport_and_https_button(self):
        result = send_challenge_photo(
            self.telegram, "123", self.photo, "bing",
            "https://leshy.example.org/browser-challenge#secret",
        )
        self.assertEqual(result["message_id"], 12)
        req, timeout = self.proxy.requests[0]
        self.assertEqual(timeout, 15)
        self.assertTrue(req.full_url.endswith("/sendPhoto"))
        self.assertEqual(req.get_method(), "POST")
        body = req.data
        self.assertIn(b'name="photo"; filename="search-verification.png"', body)
        self.assertIn(b"\x89PNG\r\n\x1a\nimg", body)
        self.assertIn(b"inline_keyboard", body)
        self.assertIn(b"leshy.example.org", body)
        self.assertNotIn(b"test-token-telegram", body)

    def test_non_https_link_is_not_added_as_telegram_button(self):
        send_challenge_photo(
            self.telegram, "123", self.photo, "bing", "/browser-challenge#secret"
        )
        self.assertNotIn(b"inline_keyboard", self.proxy.requests[0][0].data)

    def test_invalid_media_rejected_without_network_request(self):
        with self.assertRaises(ValueError):
            send_challenge_photo(self.telegram, "123", "not a PNG", "bing")
        with self.assertRaises(ValueError):
            send_challenge_photo(
                self.telegram, "123",
                "data:image/png;base64," + base64.b64encode(b"not an image").decode(),
                "bing",
            )
        self.assertFalse(self.proxy.requests)


if __name__ == "__main__":
    unittest.main()
