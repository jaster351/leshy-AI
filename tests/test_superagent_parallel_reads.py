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

if __name__=='__main__':unittest.main()
