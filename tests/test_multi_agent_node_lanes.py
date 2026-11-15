"""Regression tests for request-scoped parallel cluster lanes.

No real cluster, network, or model is started.  These tests protect the
existing /agents -> MultiAgentOrchestrator -> cluster prompt route.
"""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
import threading
import time
import unittest

from leshy.multi_agent_orchestrator import AgentReport, MultiAgentOrchestrator


def _orchestrator(node_ids: tuple[str, ...]):
    nodes = [
        SimpleNamespace(node_id=node_id, accepting_tasks=True)
        for node_id in node_ids
    ]
    cluster = SimpleNamespace(refresh=lambda: nodes)
    orchestrator = MultiAgentOrchestrator(
        SimpleNamespace(distributed_cluster=cluster)
    )
    orchestrator._shared_context = lambda owner, task: "context"
    return orchestrator


class ParallelNodeLaneTests(unittest.TestCase):
    def test_parallel_across_nodes_but_serial_within_each_node(self):
        agent = _orchestrator(("pc1", "pc2"))
        active = Counter()
        maximum = Counter()
        simultaneous = []
        lock = threading.Lock()
        rendezvous = threading.Barrier(2, timeout=3)
        first_seen = set()

        def role_run(cluster, node, role, task, context, **kwargs):
            with lock:
                active[node.node_id] += 1
                maximum[node.node_id] = max(maximum[node.node_id], active[node.node_id])
                simultaneous.append(sum(active.values()))
                first_on_node = node.node_id not in first_seen
                first_seen.add(node.node_id)
            try:
                if first_on_node:
                    rendezvous.wait(timeout=3)
                time.sleep(0.01)
                return AgentReport(
                    role=role.name, node_id=node.node_id, answer="ok"
                )
            finally:
                with lock:
                    active[node.node_id] -= 1

        agent._cluster_role = role_run
        reports = agent._run_roles(
            "design task", owner_id="web:alice", session_id="sess-a",
            request_id="request-a", priority=70,
        )
        self.assertEqual([r.role for r in reports], [r.name for r in agent.roles])
        self.assertEqual(len(reports), len(agent.roles))
        self.assertEqual(set(maximum.values()), {1})
        self.assertIn(2, simultaneous)
        self.assertEqual(active, Counter({"pc1": 0, "pc2": 0}))

    def test_role_failure_does_not_cancel_other_roles(self):
        agent = _orchestrator(("pc1", "pc2"))

        def role_run(cluster, node, role, task, context, **kwargs):
            self.assertEqual(kwargs["owner_id"], "web:alice")
            self.assertEqual(kwargs["session_id"], "sess-a")
            if role == agent.roles[0]:
                raise RuntimeError("node rejected the task")
            return AgentReport(role=role.name, node_id=node.node_id, answer="ok")

        agent._cluster_role = role_run
        reports = agent._run_roles(
            "design task", owner_id="web:alice", session_id="sess-a",
            request_id="request-b", priority=70,
        )
        self.assertEqual(len(reports), len(agent.roles))
        self.assertEqual([r.ok for r in reports], [False, True, True, True])
        self.assertIn("node rejected", reports[0].error)

    def test_duplicate_node_ids_do_not_double_book_a_node(self):
        agent = _orchestrator(("pc1", "pc1", "pc2"))
        counts = Counter()

        def role_run(cluster, node, role, task, context, **kwargs):
            counts[node.node_id] += 1
            return AgentReport(role=role.name, node_id=node.node_id, answer="ok")

        agent._cluster_role = role_run
        reports = agent._run_roles(
            "task", owner_id="web:alice", session_id="sess-a",
            request_id="request-c", priority=70,
        )
        self.assertEqual(len(reports), 4)
        self.assertEqual(counts, {"pc1": 2, "pc2": 2})

    def test_single_node_keeps_sequential_local_execution(self):
        agent = _orchestrator(("pc1",))
        calls = []
        agent._local_role = lambda role, task, context, **kwargs: (
            calls.append(role.name) or AgentReport(
                role=role.name, node_id="local", answer="ok"
            )
        )
        reports = agent._run_roles(
            "task", owner_id="web:alice", session_id="sess-a",
            request_id="request-d", priority=70,
        )
        self.assertEqual(len(reports), 4)
        self.assertEqual(calls, [role.name for role in agent.roles])


if __name__ == "__main__":
    unittest.main()
