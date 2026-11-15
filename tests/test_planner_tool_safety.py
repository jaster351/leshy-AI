"""Guard the existing planner tool loop against duplicate tool execution."""
from __future__ import annotations

import json
import unittest

from leshy.planner import Planner
from leshy.llm import ChatMessage


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []

    def chat(self, messages):
        self.seen.append(list(messages))
        return next(self.responses)


class FakeTools:
    def __init__(self, result='{"ok":true}'):
        self.result = result
        self.calls = []

    def specs(self):
        return []

    def has(self, name):
        return False

    def execute(self, name, arguments, *, context=None):
        self.calls.append((name, arguments, context.owner_id, context.session_id))
        return self.result


def tool(query):
    return json.dumps({"action": "tool", "tool": "knowledge.search",
                       "arguments": {"query": query}}, ensure_ascii=False)


class PlannerToolSafetyTests(unittest.TestCase):
    def test_identical_tool_request_is_never_executed_twice(self):
        llm = FakeLLM([tool("linux"), tool("linux")])
        tools = FakeTools()
        response = Planner(llm, tools).run("system", [ChatMessage("user", "help")],
                                           owner_id="web:one", session_id="s1")
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(tools.calls[0], ("knowledge.search", {"query": "linux"}, "web:one", "s1"))
        self.assertIn("Повторяющийся", response)

    def test_different_arguments_remain_independent(self):
        llm = FakeLLM([tool("linux"), tool("windows"),
                       '{"action":"final","answer":"готово"}'])
        tools = FakeTools()
        response = Planner(llm, tools).run("system", [ChatMessage("user", "help")])
        self.assertEqual(response, "готово")
        self.assertEqual([x[1]["query"] for x in tools.calls], ["linux", "windows"])

    def test_bad_arguments_never_reach_registry(self):
        llm = FakeLLM(['{"action":"tool","tool":"knowledge.search","arguments":"oops"}'])
        tools = FakeTools()
        response = Planner(llm, tools).run("system", [ChatMessage("user", "help")])
        self.assertIn("Некорректный", response)
        self.assertEqual(tools.calls, [])

    def test_approval_stays_blocked_without_second_model_turn(self):
        llm = FakeLLM([tool("danger")])
        tools = FakeTools('{"status":"approval_required","approval_id":"approval-7"}')
        response = Planner(llm, tools).run("system", [ChatMessage("user", "help")])
        self.assertIn("approval-7", response)
        self.assertEqual(len(llm.seen), 1)

    def test_long_tool_output_is_bounded_before_next_llm_step(self):
        llm = FakeLLM([tool("long"), '{"action":"final","answer":"готово"}'])
        tools = FakeTools("X" * 50000)
        response = Planner(llm, tools).run("system", [ChatMessage("user", "help")])
        self.assertEqual(response, "готово")
        message = llm.seen[1][-1].content
        self.assertIn("вывод инструмента сокращён", message)
        self.assertLess(len(message), 16200)


if __name__ == "__main__":
    unittest.main()
