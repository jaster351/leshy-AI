"""Regression coverage: explicit model requirements must fail closed on every cluster mode."""
from __future__ import annotations

import unittest

from leshy.cluster_orchestrator import (
    ClusterNode, ClusterOrchestrator, ClusterTask,
    COOPERATIVE_MODE, MODEL_PARALLEL_MODE,
)


def task(*, model: str = "", mode: str = COOPERATIVE_MODE) -> ClusterTask:
    return ClusterTask.create(
        origin_node_id="local", reply_to="http://127.0.0.1:8765",
        owner_id="owner-1", session_id="session-1", request_id="request-1",
        priority=70, kind="agent.prompt", payload={},
        model=model, mode=mode,
    )


def node(node_id: str, models: tuple[str, ...] = ()) -> ClusterNode:
    return ClusterNode(node_id=node_id, endpoint="http://127.0.0.1:8765",
                       cpu_threads=8, models=models)


class ExplicitClusterModelTests(unittest.TestCase):
    def test_empty_model_advertisement_rejects_explicit_model(self):
        self.assertFalse(node("empty").supports(task(model="qwen3")))

    def test_mismatched_model_rejected_in_both_modes(self):
        for mode in (COOPERATIVE_MODE, MODEL_PARALLEL_MODE):
            with self.subTest(mode=mode):
                self.assertFalse(node("wrong", ("llama",)).supports(
                    task(model="qwen3", mode=mode)))

    def test_matching_model_accepted_in_both_modes(self):
        for mode in (COOPERATIVE_MODE, MODEL_PARALLEL_MODE):
            with self.subTest(mode=mode):
                self.assertTrue(node("matching", ("qwen3",)).supports(
                    task(model="qwen3", mode=mode)))

    def test_unspecified_model_remains_compatible(self):
        self.assertTrue(node("empty").supports(task()))

    def test_placement_chooses_advertised_model_not_empty_local(self):
        cluster = ClusterOrchestrator(node("local"))
        cluster.register_peer(node("remote", ("qwen3",)))
        self.assertEqual(cluster.choose_executor(task(model="qwen3")).node_id,
                         "remote")

    def test_no_matching_model_never_falls_back_to_local(self):
        cluster = ClusterOrchestrator(node("local"))
        cluster.register_peer(node("remote", ("llama",)))
        with self.assertRaisesRegex(RuntimeError, "Нет доступного узла"):
            cluster.choose_executor(task(model="qwen3"))
        self.assertEqual(cluster.status()["inflight_by_node"], {})


if __name__ == "__main__":
    unittest.main()
