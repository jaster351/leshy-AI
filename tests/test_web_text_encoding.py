from __future__ import annotations

import unittest

from leshy.web_text_encoding import decode_web_text


class WebTextEncodingTests(unittest.TestCase):
    def test_declared_windows_1251_page_decodes_without_loss(self):
        page = '<html><head><meta charset="windows-1251"></head><body>Привет, мир!</body></html>'
        result = decode_web_text(page.encode("cp1251"), "text/html", html=True)
        self.assertEqual(result, page)

    def test_http_header_charset_is_respected(self):
        message = "Декларация независимости США — 4 июля 1776 года."
        self.assertEqual(decode_web_text(message.encode("cp1251"), "text/plain; charset=windows-1251"), message)

    def test_utf8_content_works_without_charset(self):
        message = "Проверка UTF-8, русский и English."
        self.assertEqual(decode_web_text(message.encode("utf-8"), "text/plain"), message)

    def test_invalid_bytes_cannot_be_silently_replaced(self):
        with self.assertRaises(UnicodeError):
            decode_web_text(b"<html>English only\xff\xfe</html>", "text/html", html=True)

    def test_previously_replaced_text_is_rejected(self):
        with self.assertRaises(UnicodeError):
            decode_web_text("повреждено \ufffd здесь".encode("utf-8"), "text/plain")


if __name__ == "__main__":
    unittest.main()
