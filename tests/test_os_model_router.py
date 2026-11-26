"""Регрессии выбора системных действий моделью и границ диалога."""
from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from leshy.os_control import OperatingSystemController
from leshy.os_model_router import parse_os_plan, select_os_action


class ModelOSRouterTests(unittest.TestCase):
    def setUp(self):
        self.controller = Mock()
        self.controller.enabled.return_value = True
        self.app = SimpleNamespace(os_control=self.controller)

    def test_natural_language_never_matches_legacy_trigger(self):
        controller = OperatingSystemController.__new__(OperatingSystemController)
        for text in (
            "Открой Windows и посмотри, как звучит эта фраза",
            "Как выполнить команду PowerShell?",
            "Не запускай программу",
            "Я сказал: открой файл, но это был пример",
            "Проверь, как работает Windows",
            "Выполни задачу в репозитории",
            "Давай поговорим о перезагрузке компьютера",
        ):
            with self.subTest(text=text):
                self.assertFalse(controller.is_request(text))

    def test_model_decides_dialogue_including_slash_syntax(self):
        for text in ("Привет", "Как открыть файл?", "Не удаляй файл",
                     "Вот пример: /os run echo hello", "/os status"):
            infer = Mock(return_value='{"action":"conversation","args":{}}')
            result = select_os_action(self.app, text, [], owner_id="web:local", infer=infer)
            self.assertIsNone(result)
            infer.assert_called_once()
            self.controller.handle_model_action.assert_not_called()

    def test_model_can_choose_action_without_trigger_words(self):
        infer = Mock(return_value='{"action":"system_status","args":{}}')
        result = select_os_action(self.app, "А сколько у нас места осталось?", [],
                                  owner_id="web:local", infer=infer)
        self.assertEqual(result, ("system_status", {}))
        self.controller.handle_model_action.assert_not_called()

    def test_history_is_data_and_latest_message_is_separate(self):
        infer = Mock(return_value='{"action":"conversation","args":{}}')
        rows = [{"role": "user", "content": "Не надо"},
                {"role": "assistant", "content": "Удалить файл?"},
                {"role": "user", "content": "Поговорим об удалении"}]
        select_os_action(self.app, "Не надо", rows, owner_id="web:local", infer=infer)
        messages = infer.call_args.args[0]
        self.assertEqual([x.role for x in messages], ["system", "user"])
        payload = json.loads(messages[1].content)
        self.assertEqual(payload["текущая_реплика"], "Не надо")
        self.assertEqual(len(payload["предыдущий_диалог"]), 2)
        self.assertEqual(payload["предыдущий_диалог"][-1]["role"], "assistant")

    def test_disabled_or_unverified_owner_does_not_infer(self):
        infer = Mock()
        self.controller.enabled.return_value = False
        self.assertIsNone(select_os_action(self.app, "Открой файл", [],
                                         owner_id="web:local", infer=infer))
        self.controller.enabled.return_value = True
        self.controller._authorize_owner.side_effect = PermissionError("Нет права")
        self.assertIsNone(select_os_action(self.app, "Открой файл", [],
                                         owner_id="telegram:200", infer=infer))
        infer.assert_not_called()

    def test_cancel_during_inference_drops_decision(self):
        cancel = threading.Event()
        def infer(_):
            cancel.set()
            return '{"action":"native_shell","args":{"command":"echo hello"}}'
        self.assertIsNone(select_os_action(self.app, "Сделай", [],
                                         owner_id="web:local", infer=infer, cancel=cancel))

    def test_inference_error_does_not_fall_back_to_script(self):
        infer = Mock(side_effect=RuntimeError("Модель недоступна"))
        self.assertIsNone(select_os_action(self.app, "Выполни команду", [],
                                         owner_id="web:local", infer=infer))

    def test_exact_json_and_strict_argument_schema(self):
        bad = [
            'Сейчас выполню {"action":"system_status","args":{}}',
            '```json\n{"action":"system_status","args":{}}\n```',
            '{"action":"system_status","action":"native_shell","args":{}}',
            '{"action":"native_shell","args":{"command":"echo hello","timeout":NaN}}',
            '{"action":"native_shell","args":{"command":"echo hello","approved":true}}',
            '{"action":"stop_process","args":{"pid":true}}',
            '{"action":"stop_process","args":{"pid":0}}',
            '{"action":"start_process","args":{"program":"cmd","args":[5]}}',
            '{"action":"read_text","args":{}}',
            '{"action":"delete","args":{"path":""}}',
            '{"action":"web_search","args":{}}',
            '{"action":"conversation","args":{"command":"echo hello"}}',
            '{"action":"native_shell","args":{"command":"abc\\u0000def"}}',
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_os_plan(raw))
        self.assertEqual(parse_os_plan('{"action":"read_text","args":{"path":"a.txt"}}'),
                         ("read_text", {"path": "a.txt"}))


if __name__ == "__main__":
    unittest.main()
