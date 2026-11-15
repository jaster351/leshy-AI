"""Fail-closed cluster resource placement for RAM/model requirements."""
from __future__ import annotations

import unittest

from leshy.cluster_orchestrator import ClusterNode, ClusterOrchestrator, ClusterTask


def node(name: str, ram: float) -> ClusterNode:
    return ClusterNode(
        node_id=name, endpoint="http://127.0.0.1:8765", cpu_threads=8,
        memory_available_gb=ram,
    )


def task(requirement: str) -> ClusterTask:
    return ClusterTask.create(
        origin_node_id="local", reply_to="http://127.0.0.1:8765",
        owner_id="web:owner", session_id="s", request_id="r",
        priority=70, kind="agent.prompt", payload={},
        requirements=(requirement,),
    )


class ClusterRamRequirementsTests(unittest.TestCase):
    def test_unknown_ram_does_not_satisfy_explicit_minimum(self):
        self.assertFalse(node("unknown", 0).supports(task("ram>=8")))

    def test_invalid_ram_requirement_fails_closed(self):
        self.assertFalse(node("n", 16).supports(task("ram>=oops")))
        self.assertFalse(node("n", 16).supports(task("ram>=nan")))

    def test_suitable_remote_is_used_instead_of_insufficient_local(self):
        cluster = ClusterOrchestrator(node("local", 2))
        cluster.register_peer(node("remote", 12))
        self.assertEqual(cluster.choose_executor(task("ram>=8")).node_id, "remote")

    def test_no_eligible_node_does_not_run_on_wrong_local_machine(self):
        cluster = ClusterOrchestrator(node("local", 2))
        cluster.register_peer(node("remote", 4))
        with self.assertRaisesRegex(RuntimeError, "Нет доступного узла"):
            cluster.choose_executor(task("ram>=8"))
        self.assertEqual(cluster.status()["inflight_by_node"], {})

    def test_matching_ram_is_accepted(self):
        self.assertTrue(node("local", 16).supports(task("ram>=8")))


if __name__ == "__main__":
    unittest.main()
