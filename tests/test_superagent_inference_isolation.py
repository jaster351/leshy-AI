"""The local LLM slot is short-lived; independent read-only jobs are not serialized."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from leshy.superagent import SuperAgentService


class FakeJournal:
    def begin(self, *args, **kwargs):
        pass
    def finish(self, *args, **kwargs):
        pass


class BlockingRead:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, name, args, *, ticket_id):
        self.started.set()
        if not self.release.wait(timeout=3):
            raise RuntimeError("test read timed out")
        return {"receipt_id": "local-test-receipt", "result": {"version": "test"}}


class FakeLLM:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls = []
        self.second_called = threading.Event()

    def chat(self, messages):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(.02)
            request = messages[1].content
            if "SA-A" in request and len(messages) == 2:
                value = {"action": "parallel", "tasks": [
                    {"id": "x", "tool": "runtime.version", "arguments": {}},
                ]}
            else:
                value = {"status": "ready", "result": "Исследование завершено", "question": ""}
            if "SA-B" in request:
                self.second_called.set()
            return json.dumps(value, ensure_ascii=False)
        finally:
            with self.lock:
                self.active -= 1


class InferenceIsolationTests(unittest.TestCase):
    def make_service(self):
        model = FakeLLM()
        service = object.__new__(SuperAgentService)
        service.app = SimpleNamespace(llm=model)
        service._llm_lock = threading.RLock()
        service._stop = threading.Event()
        service._inputs = lambda _: [{"kind": "request", "content": "Прочитай версию"}]
        service._ticket_working = lambda _: True
        service._journal = FakeJournal()
        read = BlockingRead()
        service._cognitive_reads = read
        return service, model, read

    def test_slow_tool_does_not_block_unrelated_llm_call(self):
        service, model, read = self.make_service()
        task_a = {"id": "SA-A", "owner_id": "web:alice",
                  "request_text": "Исследуй локальную версию", "attempt": 0}
        task_b = {"id": "SA-B", "owner_id": "web:alice",
                  "request_text": "Подготовь вывод по вопросу", "attempt": 0}
        with patch("leshy.superagent_parallel.available_worker_slots", return_value=4):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(service._execute, task_a)
                self.assertTrue(read.started.wait(timeout=2))
                try:
                    b = pool.submit(service._execute, task_b)
                    self.assertTrue(model.second_called.wait(timeout=1),
                                    "An unrelated LLM response must not wait for read I/O")
                    self.assertEqual(b.result(timeout=2)[0], "ready")
                finally:
                    read.release.set()
                self.assertEqual(a.result(timeout=3)[0], "ready")
        self.assertEqual(model.peak, 1, "The local model must stay serialized")

    def test_chat_helper_serializes_concurrent_local_model_calls(self):
        service, model, _ = self.make_service()
        with ThreadPoolExecutor(max_workers=6) as pool:
            values = list(pool.map(lambda _: service._chat_inference(
                "SA-C", [SimpleNamespace(content="system"), SimpleNamespace(content="prompt")]), range(6)))
        self.assertEqual(len(values), 6)
        self.assertEqual(model.peak, 1)


if __name__ == "__main__":
    unittest.main()
