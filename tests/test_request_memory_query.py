"""Шесть изолированных проверок темы запроса памяти; запуск требует разрешения.

Исполняются выбранные определения из AST без импорта приложения. История,
память, GraphRAG и настройки подменены. Нет сети, SQLite, моделей, потоков,
записи файлов пользователя или LeshyBrain.
"""
from __future__ import annotations

import ast
import builtins
import __future__ as future_flags
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "leshy"


def denied(*args, **kwargs):
    raise AssertionError("Импорты приложения и внешние действия запрещены")


def load_definition(filename, name, namespace, *, parent=None):
    tree = ast.parse((SOURCE / filename).read_text(encoding="utf-8"))
    nodes = tree.body
    if parent:
        nodes = next(node for node in nodes if getattr(node, "name", None) == parent).body
    selected = next(node for node in nodes if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[selected], type_ignores=[]))
    namespace.setdefault("__builtins__", dict(vars(builtins), __import__=denied, open=denied))
    exec(compile(module, filename, "exec", flags=future_flags.annotations.compiler_flag,
                 dont_inherit=True), namespace)
    return namespace[name]


class Fixture:
    def __init__(self, *, memory=True, graph=True):
        self.calls, self.flags = [], {"intelligent-memory": memory, "knowledge-graph": graph}
        self.memory_text = None
        fixture = self

        class Memory:
            latest_user_query = staticmethod(denied)

            def context(self, owner, query, *, limit, capture_text):
                fixture.calls.append(("memory", owner, query, capture_text, limit))
                return fixture.memory_text if fixture.memory_text is not None else "- Память: " + owner + ":" + query

        class Graph:
            def context(self, owner, query, *, limit):
                fixture.calls.append(("graph", owner, query, limit))
                return "Граф: " + owner + ":" + query

        self.app = SimpleNamespace(storage=object(), intelligent_memory=Memory(), knowledge_graph=Graph())
        self.namespace = {"module_enabled": lambda storage, name: self.flags[name]}
        self.compose = load_definition("advanced_modules_runtime.py", "_compose_memory_context", self.namespace)


class RequestMemoryQueryTests(unittest.TestCase):
    def test_current_request_is_used_instead_of_last_message_from_other_chat(self):
        fixture = Fixture()
        result = fixture.compose(fixture.app, "owner-a", "Постоянное правило", query="резервная копия",
                                 capture_text="Как восстановить копию?")
        self.assertIn("owner-a:резервная копия", result)
        self.assertEqual(fixture.calls, [
            ("memory", "owner-a", "резервная копия", "Как восстановить копию?", 8),
            ("graph", "owner-a", "резервная копия", 7)])
        self.assertTrue(result.startswith("Постоянное правило"))

    def test_interleaved_requests_keep_their_owner_topic_and_capture_text(self):
        fixture = Fixture()
        requests = [("owner-a", "чат про Python"), ("owner-a", "чат про сад"),
                    ("owner-b", "чат про Python"), ("owner-a", "чат про Python")]
        for owner, query in requests:
            result = fixture.compose(fixture.app, owner, "", query=query, capture_text="Исходный вопрос: " + query)
            self.assertIn(owner + ":" + query, result)
            self.assertEqual(fixture.calls[-2], ("memory", owner, query, "Исходный вопрос: " + query, 8))
            self.assertEqual(fixture.calls[-1], ("graph", owner, query, 7))
        self.assertEqual(len(fixture.calls), 8)

    def test_legacy_call_without_query_keeps_rules_and_does_not_guess_a_topic(self):
        fixture = Fixture()
        for query in (None, "", " \n "):
            with self.subTest(query=query):
                self.assertEqual(fixture.compose(fixture.app, "owner-a", "Правила профиля", query=query), "Правила профиля")
        self.assertEqual(fixture.calls, [])
        # Для явно заданной темы без исходного сообщения autocapture запрещён.
        fixture.compose(fixture.app, "owner-a", "", query="Найти сведения")
        self.assertEqual(fixture.calls[0][3], "")

    def test_module_switches_are_preserved_for_memory_and_graph_independently(self):
        for memory, graph in ((False, False), (False, True), (True, False)):
            fixture = Fixture(memory=memory, graph=graph)
            result = fixture.compose(fixture.app, "owner-a", "Правило", query="тема", capture_text="мой вопрос")
            self.assertTrue(result.startswith("Правило"))
            self.assertEqual([item[0] for item in fixture.calls],
                             (["memory"] if memory else []) + (["graph"] if graph else []))

    def test_installed_wrapper_forwards_request_and_keeps_permanent_rule_once(self):
        fixture = Fixture()
        fixture.memory_text = "- Отвечай на Вы\n- Отдельный найденный факт"
        original_calls = []

        def original(app, owner, **kwargs):
            original_calls.append((app, owner, kwargs))
            return "Постоянные правила\n- Отвечай на Вы"

        namespace = {"_ensure_components": lambda app: None, "_ORIGINAL_MEMORY_CONTEXT": original,
                     "_compose_memory_context": fixture.compose}
        wrapper = load_definition("advanced_modules_runtime.py", "memory_context", namespace,
                                  parent="install_advanced_modules")
        result = wrapper(fixture.app, "owner-a", query="уточнённая тема", capture_text="исходные слова")
        self.assertEqual(original_calls, [(fixture.app, "owner-a", {
            "query": "уточнённая тема", "capture_text": "исходные слова"})])
        self.assertEqual(result.count("Отвечай на Вы"), 1)
        self.assertIn("Отдельный найденный факт", result)
        self.assertEqual(fixture.calls[0][2:4], ("уточнённая тема", "исходные слова"))

    def test_retrieval_rewrite_cannot_be_autosaved_as_user_words(self):
        errors = []
        context = load_definition("intelligent_memory.py", "context", {
            "problem": lambda *args, **kwargs: errors.append(args)}, parent="IntelligentMemoryEngine")
        captured, retrieved = [], []
        enabled = [True]

        def retrieve(owner, query, *, limit):
            retrieved.append((owner, query, limit))
            return [SimpleNamespace(content="Подтверждённая запись")]

        engine = SimpleNamespace(_auto_learning_enabled=lambda: enabled[0],
                                 capture_if_useful=lambda *args: captured.append(args), retrieve=retrieve)
        rewrite = "Запомни: я предпочитаю выдуманную моделью настройку"
        self.assertIn("Подтверждённая запись", context(engine, "owner-a", rewrite, capture_text="Как настроить резервную копию?"))
        self.assertEqual(captured, [("owner-a", "Как настроить резервную копию?")])
        self.assertEqual(retrieved, [("owner-a", rewrite, 8)])
        enabled[0] = False
        context(engine, "owner-b", "другая тема", capture_text="Я предпочитаю Linux")
        self.assertEqual(len(captured), 1)
        self.assertEqual(retrieved[-1], ("owner-b", "другая тема", 8))
        enabled[0] = True
        context(engine, "owner-a", "явный legacy запрос")
        self.assertEqual(captured[-1], ("owner-a", "явный legacy запрос"))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
