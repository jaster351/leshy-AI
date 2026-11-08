from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from leshy.swarm_bootstrap import (
    GITHUB_MANIFEST_URL, GitHubBootstrapDirectory, validate_manifest,
)


def directory(*urls):
    return {
        "schema_version": 1, "network_id": "leshy-public-v1",
        "seeds": [
            {"id": f"entry-{i}", "url": url}
            for i, url in enumerate(urls, 1)
        ],
    }


class _Response:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n):
        return self.payload[:n]


class _Opener:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = []

    def open(self, req, *, timeout):
        self.calls.append((req.full_url, timeout))
        return _Response(self.payload)


class GitHubBootstrapTests(TestCase):
    def test_empty_catalog_does_not_claim_a_working_network(self):
        self.assertEqual(validate_manifest(directory()), ())

    def test_only_stable_public_seeds_not_private_node_information(self):
        valid = directory("https://bootstrap.example.org", "https://bootstrap2.example.org/v1/swarm")
        self.assertEqual(len(validate_manifest(valid)), 2)
        for invalid in (
            {"schema_version": 1, "network_id": "different-swarm", "seeds": []},
            {**valid, "registered_users": ["alice"]},
            directory("http://bootstrap.example.org"),
            directory("https://127.0.0.1:38865"),
            directory("https://192.168.1.1:38865"),
            directory("https://10.1.2.3:38865"),
            directory("https://bootstrap.local"),
            directory("https://user:password@bootstrap.example.org"),
            directory("https://bootstrap.example.org/?token=secret"),
            directory("https://bootstrap.example.org/#token"),
            directory("https://bootstrap.example.org/malicious"),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_manifest(invalid)

    def test_local_pilot_requires_explicit_dev_allow_http(self):
        doc = directory("http://127.0.0.1:38865")
        with self.assertRaises(ValueError):
            validate_manifest(doc)
        self.assertEqual(
            validate_manifest(doc, allow_local_http=True),
            ("http://127.0.0.1:38865",),
        )

    def test_read_only_github_download_and_durable_offline_cache(self):
        with TemporaryDirectory() as tmp:
            payload = json.dumps(directory("https://bootstrap.example.org")).encode()
            opener = _Opener(payload)
            entry = GitHubBootstrapDirectory(Path(tmp))
            with patch("leshy.swarm_bootstrap.request.build_opener", return_value=opener):
                self.assertEqual(entry.refresh(), ("https://bootstrap.example.org",))
            self.assertEqual(opener.calls, [(GITHUB_MANIFEST_URL, 6)])
            self.assertEqual(entry.source, "github")
            recovered = GitHubBootstrapDirectory(Path(tmp))
            self.assertEqual(recovered.cached(), ("https://bootstrap.example.org",))
            self.assertEqual(recovered.source, "local_cache")

    def test_failed_or_malicious_update_retains_approved_cache(self):
        with TemporaryDirectory() as tmp:
            entry = GitHubBootstrapDirectory(tmp)
            entry.cache_path.parent.mkdir(parents=True, exist_ok=True)
            stored = json.dumps(directory("https://bootstrap.example.org")).encode()
            entry.cache_path.write_bytes(stored)
            self.assertEqual(entry.cached(), ("https://bootstrap.example.org",))
            opener = _Opener(json.dumps({**directory(), "private_chat": "secret"}).encode())
            with patch("leshy.swarm_bootstrap.request.build_opener", return_value=opener):
                self.assertEqual(entry.refresh(), ("https://bootstrap.example.org",))
            self.assertEqual(entry.cache_path.read_bytes(), stored)
            self.assertEqual(entry.last_error, "ValueError")


if __name__ == "__main__":
    import unittest
    unittest.main()
