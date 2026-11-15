from __future__ import annotations

import threading
import unittest

from leshy.cluster_orchestrator import (
    ClusterNode,
    ClusterOrchestrator,
    ClusterResult,
    ClusterTask,
    cluster_public_summary,
)


def task(number: str) -> ClusterTask:
    return ClusterTask.create(
        origin_node_id="local",
        reply_to="http://127.0.0.1:8765",
        owner_id="web:local",
        session_id="session",
        request_id=number,
        priority=100,
        kind="agent.prompt",
        payload={"prompt": "hello"},
    )


def node(name: str, *, gpu: bool = False, queue: int = 0) -> ClusterNode:
    return ClusterNode(
        node_id=name,
        endpoint="http://127.0.0.1:8765",
        cpu_threads=8,
        gpu_names=("test-gpu",) if gpu else (),
        memory_available_gb=16.0,
        queue_depth=queue,
        load=0.0,
    )


class ClusterBalancedRoutingTests(unittest.TestCase):
    def test_busy_fast_gpu_does_not_monopolize_work(self):
        orchestrator = ClusterOrchestrator(node("local"))
        orchestrator.register_peer(node("fast-gpu", gpu=True, queue=2))
        orchestrator.register_peer(node("idle-cpu"))
        self.assertEqual(orchestrator.choose_executor(task("1")).node_id, "idle-cpu")

    def test_simultaneous_requests_reserve_different_remote_nodes(self):
        waiting = threading.Event()
        release = threading.Event()

        class Transport:
            calls: list[str] = []

            def submit(self, destination, work):
                self.calls.append(destination.node_id)
                if destination.node_id == "a":
                    waiting.set()
                    if not release.wait(timeout=3):
                        raise TimeoutError("test release was not signaled")
                return ClusterResult(
                    task_id=work.task_id,
                    origin_node_id=work.origin_node_id,
                    executor_node_id=destination.node_id,
                    request_id=work.request_id,
                    ok=True,
                )

        transport = Transport()
        orchestrator = ClusterOrchestrator(node("local"), transport=transport)
        orchestrator.register_peer(node("a", gpu=True))
        orchestrator.register_peer(node("b", gpu=True))
        outputs: list[ClusterResult] = []

        def run_first():
            outputs.append(orchestrator.execute_or_delegate(task("first"), lambda _: self.fail("local route")))

        thread = threading.Thread(target=run_first)
        thread.start()
        try:
            self.assertTrue(waiting.wait(timeout=3))
            second = orchestrator.execute_or_delegate(task("second"), lambda _: self.fail("local route"))
            self.assertEqual(second.executor_node_id, "b")
        finally:
            release.set()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual({result.executor_node_id for result in outputs}, {"a"})
        self.assertEqual(transport.calls, ["a", "b"])
        self.assertEqual(orchestrator.status()["inflight_by_node"], {})

    def test_reservation_released_after_transport_error(self):
        class Transport:
            def submit(self, destination, work):
                raise ConnectionError("offline")

        orchestrator = ClusterOrchestrator(node("local"), transport=Transport())
        orchestrator.register_peer(node("remote", gpu=True))
        with self.assertRaises(ConnectionError):
            orchestrator.execute_or_delegate(task("1"), lambda _: self.fail("local route"))
        self.assertEqual(orchestrator.status()["inflight_by_node"], {})

    def test_public_summary_returns_only_aggregate_not_identity(self):
        raw = {
            "cluster_id": "private-cluster",
            "secret_path": "C:/private/key",
            "peers": [
                {"node_id": "private-a", "endpoint": "http://10.0.0.1:8765",
                 "cpu_threads": 8, "queue_depth": 1, "memory_total_gb": 16,
                 "gpu_names": ["GPU"], "npu_names": []},
                {"node_id": "private-b", "endpoint": "http://10.0.0.2:8765",
                 "cpu_threads": 4, "queue_depth": 0, "memory_total_gb": 8,
                 "gpu_names": [], "npu_names": ["NPU"]},
            ],
        }
        summary = cluster_public_summary(raw, enabled=True)
        self.assertEqual(summary["node_count"], 2)
        self.assertEqual(summary["busy_nodes"], 1)
        self.assertEqual(summary["available_nodes"], 1)
        self.assertEqual(summary["cpu_threads"], 12)
        self.assertEqual(summary["ram_total_gb"], 24.0)
        self.assertEqual(summary["gpu_count"], 1)
        self.assertEqual(summary["npu_count"], 1)
        self.assertNotIn("private-a", str(summary))
        self.assertNotIn("10.0.0.1", str(summary))
        self.assertNotIn("private-cluster", str(summary))
        self.assertNotIn("secret_path", summary)
        self.assertEqual(cluster_public_summary(raw, enabled=False)["node_count"], 0)


if __name__ == "__main__":
    unittest.main()
