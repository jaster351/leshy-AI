from __future__ import annotations

import threading
import unittest

from leshy.telegram_exact_once import finalize_answer_exact_once


class _Bridge:
    final_retry_delays = ()

    def __init__(self, *, edit_result: bool):
        self._stop = threading.Event()
        self.edit_result = edit_result
        self.sent: list[tuple[str, dict]] = []
        self.edits = 0

    @staticmethod
    def _approval_keyboard(_approval):
        return None

    def _edit_stream_message(self, *args, **kwargs):
        self.edits += 1
        return self.edit_result

    def _send_with_retry(self, method: str, payload: dict, *, timeout: int = 10):
        self.sent.append((method, dict(payload)))
        return {"message_id": 999}


class TelegramExactOnceRegressionTests(unittest.TestCase):
    def test_unconfirmed_final_edit_does_not_send_duplicate_first_chunk(self):
        bridge = _Bridge(edit_result=False)
        message_id = finalize_answer_exact_once(
            bridge,
            123,
            77,
            "Готовый ответ Лешего",
            55,
        )
        self.assertEqual(message_id, 77)
        self.assertGreaterEqual(bridge.edits, 1)
        self.assertEqual(bridge.sent, [])

    def test_no_preview_sends_first_answer_once(self):
        bridge = _Bridge(edit_result=False)
        message_id = finalize_answer_exact_once(
            bridge,
            123,
            0,
            "Готовый ответ Лешего",
            55,
        )
        self.assertEqual(message_id, 999)
        self.assertEqual(len(bridge.sent), 1)
        self.assertEqual(bridge.sent[0][0], "sendMessage")
        self.assertEqual(bridge.sent[0][1]["reply_parameters"]["message_id"], 55)


if __name__ == "__main__":
    unittest.main()
