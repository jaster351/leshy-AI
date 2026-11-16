"""Regression coverage for external model search; no live HTTP needed."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from leshy.model_repository_search import search_hub_models


_MODELS = [
    {
        "id": "Qwen/Qwen3.5-9B", "createdAt": "2026-02-16T09:30:00Z",
        "lastModified": "2026-10-15T00:00:00Z", "tags": ["text-generation"],
        "downloads": 120, "pipeline_tag": "text-generation",
    },
    {
        "id": "Example/Code-Coder-7B", "createdAt": "2026-11-10T10:00:00Z",
        "lastModified": "2026-11-11T00:00:00Z",
        "tags": ["gguf", "code", "license:apache-2.0"], "downloads": 12,
    },
    {
        "id": "Example/Legal-Law-7B", "createdAt": "2026-11-14T10:00:00Z",
        "tags": ["legal", "law", "text-generation"], "downloads": 1,
    },
    {
        "id": "Example/Legal-UnknownDate", "tags": ["legal"],
    },
]


class _HubReply:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.data

    def geturl(self):
        return "https://huggingface.co/api/models?sort=createdAt"


class ModelRepositorySearchTests(unittest.TestCase):
    def test_name_query_reaches_real_repository_search_endpoint(self):
        requests = []

        def fetch(request, timeout):
            requests.append((request.full_url, timeout))
            return _HubReply(_MODELS)

        with patch("leshy.model_repository_search.urlopen", side_effect=fetch):
            result = search_hub_models(name="qwen3.5")
        self.assertEqual([r["id"] for r in result["items"]], ["Qwen/Qwen3.5-9B"])
        params = parse_qs(urlsplit(requests[0][0]).query)
        self.assertEqual(params["search"], ["qwen3.5"])
        self.assertTrue(result["items"][0]["url"].startswith("https://huggingface.co/Qwen/"))
        self.assertFalse(result["exhaustive"])

    def test_range_is_inclusive_and_uses_publication_not_last_update(self):
        with patch("leshy.model_repository_search.urlopen", return_value=_HubReply(_MODELS)):
            result = search_hub_models(date_from="2026-11-10", date_to="2026-11-14")
        self.assertEqual(
            [r["id"] for r in result["items"]],
            ["Example/Code-Coder-7B", "Example/Legal-Law-7B"],
        )

    def test_profession_is_metadata_filter_not_claim_of_expertise(self):
        with patch("leshy.model_repository_search.urlopen", return_value=_HubReply(_MODELS)):
            result = search_hub_models(specialty="legal")
        self.assertEqual(
            [r["id"] for r in result["items"]],
            ["Example/Legal-Law-7B", "Example/Legal-UnknownDate"],
        )
        self.assertIn("названию и тегам", result["note"])

    def test_author_filter_and_gguf_status(self):
        with patch("leshy.model_repository_search.urlopen", return_value=_HubReply(_MODELS)):
            result = search_hub_models(author="example", specialty="programming")
        self.assertEqual([r["id"] for r in result["items"]], ["Example/Code-Coder-7B"])
        self.assertTrue(result["items"][0]["gguf_tag"])

    def test_invalid_date_and_filters_are_rejected_without_network(self):
        with patch("leshy.model_repository_search.urlopen") as fetch:
            for kw in [
                {"date_from": "2026-11-20", "date_to": "2026-11-01"},
                {"date_from": "2026-99-01"},
                {"specialty": "unknown"},
                {"author": "../../etc/passwd"},
                {"limit": 10000},
            ]:
                with self.subTest(kw=kw), self.assertRaises(ValueError):
                    search_hub_models(**kw)
            fetch.assert_not_called()

    def test_empty_results_are_not_invented(self):
        with patch("leshy.model_repository_search.urlopen", return_value=_HubReply([])):
            result = search_hub_models(name="something-that-does-not-exist")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
