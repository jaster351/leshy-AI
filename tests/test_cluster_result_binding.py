"""Prevent mixing concurrent requests and spoofing executors on the cluster."""
from __future__ import annotations

import unittest

from leshy.cluster_orchestrator import ClusterNode, ClusterOrchestrator, ClusterResult, ClusterTask


def node(name: str, *, busy: bool = False) -> ClusterNode:
    return ClusterNode(
        node_id=name, endpoint="http://127.0.0.1:8765", cpu_threads=8,
        gpu_names=("gpu",) if name != "local" else (),
        queue_depth=20 if busy else 0,
    )


def request(label: str) -> ClusterTask:
    return ClusterTask.create(
        origin_node_id="local", reply_to="http://127.0.0.1:8765",
        owner_id="web:owner", session_id="session",
        request_id=label, priority=70, kind="agent.prompt", payload={"prompt": label},
    )


class ClusterResultBindingTests(unittest.TestCase):
    def run_case(self, field, mismatch):
        class Transport:
            def submit(self, destination, task):
                result = ClusterResult(
                    task_id=task.task_id, origin_node_id=task.origin_node_id,
                    executor_node_id=destination.node_id, request_id=task.request_id,
                    ok=True, payload={"answer": "done"},
                )
                setattr(result, field, mismatch)
                return result

        origin = ClusterOrchestrator(node("local", busy=True), transport=Transport())
        origin.register_peer(node("remote"))
        with self.assertRaisesRegex(RuntimeError, "несовпадающим"):
            origin.execute_or_delegate(request("r1"), lambda _: self.fail("local work"))
        self.assertEqual(origin.status()["inflight_by_node"], {})

    def test_wrong_request_id_is_rejected(self):
        self.run_case("request_id", "r2")

    def test_wrong_executor_is_rejected(self):
        self.run_case("executor_node_id", "different-peer")

    def test_wrong_task_is_rejected(self):
        self.run_case("task_id", "foreign-task")

    def test_valid_remote_result_passes(self):
        class Transport:
            def submit(self, destination, task):
                return ClusterResult(
                    task_id=task.task_id, origin_node_id=task.origin_node_id,
                    executor_node_id=destination.node_id, request_id=task.request_id,
                    ok=True, payload={"answer": "done"},
                )

        origin = ClusterOrchestrator(node("local", busy=True), transport=Transport())
        origin.register_peer(node("remote"))
        result = origin.execute_or_delegate(request("r1"), lambda _: self.fail("local work"))
        self.assertEqual(result.executor_node_id, "remote")
        self.assertEqual(result.payload["answer"], "done")
        self.assertEqual(origin.status()["inflight_by_node"], {})


if __name__ == "__main__":
    unittest.main()
