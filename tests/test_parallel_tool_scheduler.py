from __future__ import annotations

import json
import threading
import time
import unittest

from leshy.parallel_tool_scheduler import ParallelPlanError, execute_parallel_plan


def task(name, query, *deps):
    return {"id": name, "tool": "knowledge.search", "arguments": {"query": query}, "depends_on": list(deps)}


class FakeTools:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.active = 0
        self.maximum = 0
        self.calls = []
        self.lock = threading.Lock()

    def execute(self, name, args, *, context):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls.append((name, args["query"], context))
        try:
            time.sleep(0.02)
            if args["query"] in self.failures:
                raise OSError("never expose secrets in errors")
            return json.dumps({"query": args["query"]})
        finally:
            with self.lock:
                self.active -= 1


class ParallelPlanTests(unittest.TestCase):
    def test_parallelism_and_dependency_order(self):
        tools = FakeTools()
        plan = [task("A", "a"), task("B", "b"), task("C", "c", "A", "B")]
        result = execute_parallel_plan(plan, tools, context="owner", seen_signatures=set(), max_workers=2)
        self.assertEqual(tools.maximum, 2)
        self.assertEqual([x["id"] for x in result], ["A", "B", "C"])
        self.assertEqual([x["status"] for x in result], ["ok"] * 3)
        self.assertEqual([x[1] for x in tools.calls][-1], "c")

    def test_failed_dependency_is_blocked_but_independent_completes(self):
        tools = FakeTools({"a"})
        result = execute_parallel_plan([task("A", "a"), task("B", "b"), task("C", "c", "A")],
                                       tools, context=None, seen_signatures=set())
        self.assertEqual([x["status"] for x in result], ["failed", "ok", "blocked"])
        self.assertEqual([x[1] for x in tools.calls], ["a", "b"] if tools.calls[0][1] == "a" else ["b", "a"])

    def test_cycle_rejected_without_tool_execution(self):
        tools = FakeTools()
        with self.assertRaisesRegex(ParallelPlanError, "цикл"):
            execute_parallel_plan([task("A", "a", "B"), task("B", "b", "A")], tools,
                                  context=None, seen_signatures=set())
        self.assertEqual(tools.calls, [])

    def test_mutation_rejected_without_tool_execution(self):
        tools = FakeTools()
        with self.assertRaises(ParallelPlanError):
            execute_parallel_plan([{"id": "x", "tool": "workspace.write", "arguments": {"path": "x"}}],
                                  tools, context=None, seen_signatures=set())
        self.assertEqual(tools.calls, [])

    def test_duplicate_rejected_before_any_execution(self):
        tools = FakeTools()
        with self.assertRaisesRegex(ParallelPlanError, "Повторяющийся"):
            execute_parallel_plan([task("A", "same"), task("B", "same")],
                                  tools, context=None, seen_signatures=set())
        self.assertEqual(tools.calls, [])

    def test_nonstring_tool_rejected_without_execution(self):
        tools = FakeTools()
        with self.assertRaises(ParallelPlanError):
            execute_parallel_plan([{"id": "x", "tool": [], "arguments": {}}],
                                  tools, context=None, seen_signatures=set())
        self.assertEqual(tools.calls, [])

    def test_bounded_workers(self):
        tools = FakeTools()
        plan = [task(f"step_{i}", f"q{i}") for i in range(15)]
        execute_parallel_plan(plan, tools, context=None, seen_signatures=set(), max_workers=99)
        self.assertLessEqual(tools.maximum, 24)
        self.assertGreaterEqual(tools.maximum, 2)

    def test_cancelled_plan_makes_no_calls(self):
        tools = FakeTools()
        stop = threading.Event()
        stop.set()
        result = execute_parallel_plan([task("A", "a")], tools, context=None,
                                       seen_signatures=set(), cancel_event=stop)
        self.assertEqual(result[0]["status"], "cancelled")
        self.assertEqual(tools.calls, [])


    def test_fast_descendant_starts_without_waiting_for_slow_sibling(self):
        from concurrent.futures import ThreadPoolExecutor
        slow_started = threading.Event()
        fast_descendant = threading.Event()
        release_slow = threading.Event()

        class Tools:
            def execute(self, name, args, *, context):
                if args["query"] == "slow":
                    slow_started.set()
                    if not release_slow.wait(timeout=3):
                        raise TimeoutError("slow sibling never released")
                if args["query"] == "child":
                    fast_descendant.set()
                return "{}"

        plan = [task("slow", "slow"), task("fast", "fast"),
                task("child", "child", "fast")]
        with ThreadPoolExecutor(max_workers=1) as observer:
            future = observer.submit(execute_parallel_plan, plan, Tools(),
                                     context=None, seen_signatures=set(), max_workers=2)
            try:
                self.assertTrue(slow_started.wait(timeout=1))
                self.assertTrue(fast_descendant.wait(timeout=1),
                                "child should start before unrelated slow sibling finishes")
            finally:
                release_slow.set()
            self.assertEqual([x["status"] for x in future.result(timeout=3)],
                             ["ok", "ok", "ok"])

    def test_approval_required_blocks_only_descendants(self):
        class Tools:
            def execute(self, name, args, *, context):
                if args["query"] == "needsapproval":
                    return '{"status":"approval_required"}'
                return '{"status":"success"}'

        result = execute_parallel_plan([
            task("a", "needsapproval"),
            task("b", "independent"),
            task("c", "dependent", "a"),
        ], Tools(), context=None, seen_signatures=set(), max_workers=3)
        self.assertEqual([x["status"] for x in result], ["blocked", "ok", "blocked"])

    def test_cancel_blocks_new_descendants_not_active_reads(self):
        cancellation = threading.Event()
        class Tools:
            def execute(self, name, args, *, context):
                if args["query"] == "first":
                    cancellation.set()
                return "{}"
        result = execute_parallel_plan([
            task("a", "first"), task("b", "child", "a"),
        ], Tools(), context=None, seen_signatures=set(), max_workers=2,
                                       cancel_event=cancellation)
        self.assertEqual([x["status"] for x in result], ["ok", "cancelled"])


if __name__ == "__main__":
    unittest.main()
