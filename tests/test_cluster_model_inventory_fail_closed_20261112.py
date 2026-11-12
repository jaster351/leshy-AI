"""Regression: explicit model routing must fail closed for unknown inventories."""

import unittest

from leshy.cluster_orchestrator import ClusterNode, ClusterTask, COOPERATIVE_MODE, MODEL_PARALLEL_MODE


class ClusterModelInventoryTests(unittest.TestCase):
    def _task(self, mode=COOPERATIVE_MODE, model="qwen3:8b"):
        return ClusterTask.create(
            origin_node_id="origin", reply_to="origin", owner_id="owner-a",
            session_id="session-a", request_id="request-a", priority=1,
            kind="chat", payload={}, mode=mode, model=model,
        )

    def test_unknown_inventory_rejected_for_explicit_model(self):
        node = ClusterNode(node_id="node", endpoint="http://127.0.0.1", cpu_threads=4)
        self.assertFalse(node.supports(self._task()))

    def test_unknown_inventory_rejected_in_model_parallel_mode(self):
        node = ClusterNode(node_id="node", endpoint="http://127.0.0.1", cpu_threads=4)
        self.assertFalse(node.supports(self._task(MODEL_PARALLEL_MODE)))

    def test_matching_inventory_accepted(self):
        node = ClusterNode(node_id="node", endpoint="http://127.0.0.1", cpu_threads=4, models=("qwen3:8b",))
        self.assertTrue(node.supports(self._task()))

    def test_other_model_rejected(self):
        node = ClusterNode(node_id="node", endpoint="http://127.0.0.1", cpu_threads=4, models=("other",))
        self.assertFalse(node.supports(self._task()))

    def test_no_model_requirement_allows_unknown_inventory(self):
        node = ClusterNode(node_id="node", endpoint="http://127.0.0.1", cpu_threads=4)
        self.assertTrue(node.supports(self._task(model="")))

    def test_disabled_node_rejected_even_with_matching_model(self):
        node = ClusterNode(node_id="node", endpoint="http://127.0.0.1", cpu_threads=4, models=("qwen3:8b",), accepting_tasks=False)
        self.assertFalse(node.supports(self._task()))


if __name__ == "__main__":
    unittest.main()
