"""Regression: malformed nonempty swarm catalog must not revoke healthy seeds.

These tests are intentionally not executed by the autonomous workflow.
"""
import unittest
from unittest.mock import Mock, patch

from leshy.swarm_membership import SwarmMembership


class SwarmDirectoryEntriesRegression(unittest.TestCase):
    def membership(self, payload):
        member = object.__new__(SwarmMembership)
        member._manual_configured = False
        member._allow_local_http = False
        member._last_directory_refresh = 0.0
        member._last_error = ""
        member._seed_urls = ("https://known.example/v1/swarm/hello",)
        member._seed_index = 0
        member._endpoint = member._seed_urls[0]
        member._directory = Mock()
        member._directory.refresh.return_value = payload
        member._directory.last_error = ""
        member._directory.source = "github_catalog"
        return member

    def test_malformed_nonempty_catalog_preserves_known_seed(self):
        for payload in ([None], ["http://invalid.example"], [123, {}]):
            with self.subTest(payload=payload):
                member = self.membership(payload)
                with patch("leshy.swarm_membership.problem"):
                    member._discover_seeds()
                self.assertEqual(member._seed_urls, ("https://known.example/v1/swarm/hello",))
                self.assertEqual(member._endpoint, "https://known.example/v1/swarm/hello")
                self.assertEqual(member._last_error, "invalid_directory_entries")

    def test_explicit_empty_catalog_revokes_seed(self):
        member = self.membership([])
        with patch("leshy.swarm_membership.event"):
            member._discover_seeds()
        self.assertEqual(member._seed_urls, ())
        self.assertEqual(member._endpoint, "")

    def test_mixed_catalog_keeps_valid_and_drops_invalid(self):
        member = self.membership(["not-a-url", "https://fresh.example"])
        with patch("leshy.swarm_membership.event"):
            member._discover_seeds()
        self.assertEqual(member._seed_urls, ("https://fresh.example/v1/swarm/hello",))
        self.assertEqual(member._last_error, "")


if __name__ == "__main__":
    unittest.main()
