"""Regression checks: cluster membership must never become user authentication."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from leshy.cluster_orchestrator import ClusterResult, ClusterTask
from leshy.distributed_cluster import DistributedClusterRuntime, _ALLOWED_REMOTE_KINDS


class ClusterPersonalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.cluster = DistributedClusterRuntime.__new__(DistributedClusterRuntime)
        self.cluster.identity = SimpleNamespace(node_id="local-node")
        self.cluster.verify_request = Mock()
        self.cluster._execute_local = Mock(return_value=ClusterResult(
            task_id="task-1", origin_node_id="peer-node", executor_node_id="local-node",
            request_id="request", ok=True, payload={"status": "ok"},
        ))
        self.headers = {"X-Leshy-Cluster-Node": "peer-node"}

    def task(self, *, kind="cluster.ping", origin="peer-node", owner="victim"):
        return {
            "task": {
                "task_id": "task-1", "origin_node_id": origin,
                "owner_id": owner, "session_id": "victim-session",
                "request_id": "request", "kind": kind,
                "payload": {}, "created_at": __import__("time").time(),
            },
        }

    def test_remote_owner_is_not_a_local_owner(self):
        result = self.cluster.handle_remote_task(self.task(), self.headers)
        self.assertTrue(result["ok"])
        forwarded = self.cluster._execute_local.call_args.args[0]
        self.assertEqual(forwarded.owner_id, "cluster-peer:peer-node")
        self.assertEqual(forwarded.session_id, "")

    def test_legacy_graph_search_is_not_remotely_available(self):
        self.assertNotIn("graph.search", _ALLOWED_REMOTE_KINDS)
        with self.assertRaises(PermissionError):
            self.cluster.handle_remote_task(self.task(kind="graph.search"), self.headers)
        self.cluster._execute_local.assert_not_called()

    def test_reject_mismatched_source_identity(self):
        with self.assertRaises(PermissionError):
            self.cluster.handle_remote_task(self.task(origin="other-node"), self.headers)
        self.cluster._execute_local.assert_not_called()

    def test_public_knowledge_results_strip_sensitive_metadata(self):
        knowledge = Mock()
        knowledge.search.return_value = [
            {"topic": "private", "claim": "Home details", "source_url": "file:///home/a",
             "metadata_json": "TOKEN-NEVER-EXPORT"},
            {"topic": "Tech", "claim": "A public technical guide",
             "source_url": "https://example.org/reference", "source_title": "Guide",
             "metadata_json": "SECRET-METADATA"},
            {"topic": "Contact", "claim": "Call support@example.org",
             "source_url": "https://example.org/contact", "source_title": "Contact"},
        ]
        self.cluster.app = SimpleNamespace(knowledge=knowledge)
        remote = ClusterTask.create(
            origin_node_id="peer-node", reply_to="", owner_id="cluster-peer:peer-node",
            session_id="", request_id="r", priority=1,
            kind="knowledge.search", payload={"query": "technical"},
        )
        # Exercise actual local executor, not mock dispatcher.
        result = DistributedClusterRuntime._execute_local(self.cluster, remote)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.payload["items"]), 1)
        self.assertEqual(result.payload["items"][0]["topic"], "Tech")
        self.assertNotIn("METADATA", str(result.payload))
        self.assertNotIn("TOKEN", str(result.payload))

    def test_user_prompt_remains_local_without_owner_consent(self):
        local = ClusterResult(
            task_id="x", origin_node_id="local-node", executor_node_id="local-node",
            request_id="r", ok=True, payload={"answer": "local"},
        )
        self.cluster.refresh = Mock()
        self.cluster.local_node = SimpleNamespace(endpoint="http://127.0.0.1:8765")
        self.cluster.orchestrator = Mock()
        self.cluster.orchestrator.execute_or_delegate.side_effect = AssertionError(
            "must not send user context to peer")
        self.cluster._execute_local.return_value = local
        result = self.cluster.submit_agent_prompt(
            owner_id="private-user", session_id="private-session", request_id="r",
            role="helper", system="private", prompt="private message",
        )
        self.assertIs(result, local)
        self.cluster.orchestrator.execute_or_delegate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
