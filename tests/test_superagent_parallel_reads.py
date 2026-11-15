"""The active SuperAgent route must journal each parallel tool call safely."""
from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace

from leshy.superagent_parallel import parse_parallel_read_request, run_superagent_parallel_reads
from leshy.parallel_tool_scheduler import ParallelPlanError


class Journal:
    def __init__(self):
        self.started = []
        self.finished = []
        self.lock = threading.Lock()
    def begin(self, ticket_id, attempt, index, name, args):
        with self.lock: self.started.append((ticket_id, attempt, index, name))
    def finish(self, ticket_id, attempt, index, *, status, **kwargs):
        with self.lock: self.finished.append((ticket_id, attempt, index, status))


class Executor:
    def __init__(self, fails=()):
        self.calls = []
        self.fails = set(fails)
    def execute(self, name, args, *, ticket_id):
        self.calls.append((name, ticket_id))
        if name in self.fails:
            raise RuntimeError('offline')
        return {'receipt_id': name + '-receipt', 'result': {'value': name}}


class ParallelSuperAgentTests(unittest.TestCase):
    def make_service(self, fails=()):
        journal=Journal()
        reads=Executor(fails)
        service=SimpleNamespace(_journal=journal, _cognitive_reads=reads,
                                _ticket_working=lambda t: True)
        return service,journal,reads

    def test_parallel_cognitive_tools_have_unique_receipt_steps(self):
        service,journal,reads=self.make_service()
        tasks=[{'id':'a','tool':'knowledge.search','arguments':{'query':'alpha'}},
               {'id':'b','tool':'runtime.version','arguments':{}}]
        out=run_superagent_parallel_reads(service,'SA-owner',1,0,tasks,set())
        self.assertEqual([o['status'] for o in out], ['ok','ok'])
        self.assertEqual({row[2] for row in journal.started},{1,2})
        self.assertEqual({row[3] for row in journal.finished},{'success'})
        self.assertEqual(len(reads.calls),2)

    def test_failure_does_not_claim_success_receipt(self):
        service,journal,reads=self.make_service({'runtime.version'})
        out=run_superagent_parallel_reads(service,'SA-owner',1,0,
            [{'id':'a','tool':'runtime.version','arguments':{}},
             {'id':'b','tool':'knowledge.search','arguments':{'query':'alpha'}}],set())
        self.assertEqual([o['status'] for o in out],['failed','ok'])
        self.assertEqual({row[3] for row in journal.finished},{'failed','success'})

    def test_unsafe_tool_cannot_be_scheduled(self):
        service,journal,reads=self.make_service()
        with self.assertRaises(ParallelPlanError):
            run_superagent_parallel_reads(service,'SA-owner',1,0,
                 [{'id':'a','tool':'workspace.write','arguments':{'path':'x'}}],set())
        self.assertEqual(journal.started,[])
        self.assertEqual(reads.calls,[])

    def test_cancelled_ticket_does_not_begin_tools(self):
        service,journal,reads=self.make_service()
        service._ticket_working=lambda _:False
        out=run_superagent_parallel_reads(service,'SA-owner',1,0,
                 [{'id':'a','tool':'runtime.version','arguments':{}}],set())
        self.assertEqual(out[0]['status'],'cancelled')
        self.assertEqual(journal.started,[])

    def test_parse_parallel_requires_exact_full_json(self):
        self.assertIsNone(parse_parallel_read_request('please do {"action":"parallel"}'))
        self.assertIsNone(parse_parallel_read_request('{"action":"tool"}'))
        self.assertIsNotNone(parse_parallel_read_request('{"action":"parallel","tasks":[]}'))


    def test_more_than_eight_independent_reads_when_ram_allows(self):
        from unittest.mock import patch
        barrier = threading.Barrier(9)
        active = 0
        peak = 0
        seen = 0
        lock = threading.Lock()

        class ConcurrentExecutor:
            def execute(self, name, args, *, ticket_id):
                nonlocal active, peak, seen
                with lock:
                    active += 1
                    seen += 1
                    peak = max(peak, active)
                    order = seen
                try:
                    if order <= 9:
                        barrier.wait(timeout=3)
                    return {"receipt_id": args["query"], "result": {"ok": True}}
                finally:
                    with lock:
                        active -= 1

        service, journal, _ = self.make_service()
        service._cognitive_reads = ConcurrentExecutor()
        tasks = [
            {"id": f"s{i}", "tool": "knowledge.search",
             "arguments": {"query": f"topic{i}"}, "depends_on": []}
            for i in range(12)
        ]
        with patch('leshy.superagent_parallel.available_worker_slots', return_value=12):
            out = run_superagent_parallel_reads(service, "SA-owner", 1, 0, tasks, set())
        self.assertEqual([item["status"] for item in out], ["ok"] * 12)
        self.assertGreaterEqual(peak, 9)
        self.assertEqual(len(journal.finished), 12)

    def test_memory_pressure_denies_new_reads_before_journaling(self):
        from unittest.mock import patch
        service, journal, reads = self.make_service()
        with patch('leshy.superagent_parallel.available_worker_slots', return_value=0):
            with self.assertRaisesRegex(ParallelPlanError, "памяти"):
                run_superagent_parallel_reads(service, 'SA-owner', 1, 0, [
                    {'id': 's1', 'tool': 'runtime.version', 'arguments': {}},
                ], set())
        self.assertFalse(journal.started)
        self.assertFalse(reads.calls)

if __name__=='__main__':unittest.main()
