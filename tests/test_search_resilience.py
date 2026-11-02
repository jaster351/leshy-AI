from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types
import unittest

from leshy.research import DdgsSearch, ResearchEngine, SearchUnavailable
from leshy.storage import Storage


class _DummyKnowledge:
    def add_candidate(self, **kwargs):
        return 1

    def verify_topic(self, query: str) -> dict:
        return {}


class _UnavailableSearch:
    def search(self, query: str, limit: int = 5):
        raise SearchUnavailable("сеть временно недоступна")


class SearchResilienceRegressionTests(unittest.TestCase):
    def test_ddgs_switches_backend_after_timeout(self):
        calls: list[str] = []

        class FakeDDGS:
            def __init__(self, timeout: int = 5):
                self.timeout = timeout

            def text(self, query: str, *, max_results: int, backend: str):
                calls.append(backend)
                if backend == "duckduckgo":
                    raise RuntimeError("operation timed out")
                if backend == "bing":
                    return [{
                        "title": "Рабочий резерв",
                        "href": "https://example.org/result",
                        "body": "Проверяемый результат резервного поискового backend.",
                    }]
                return []

        old = sys.modules.get("ddgs")
        module = types.ModuleType("ddgs")
        module.DDGS = FakeDDGS
        sys.modules["ddgs"] = module
        try:
            results = DdgsSearch(timeout=3, backends=("duckduckgo", "bing", "brave")).search("test", limit=5)
        finally:
            if old is None:
                sys.modules.pop("ddgs", None)
            else:
                sys.modules["ddgs"] = old

        self.assertEqual(calls, ["duckduckgo", "bing"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://example.org/result")

    def test_provider_outage_requeues_task_instead_of_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(Path(temp) / "leshy.sqlite3")
            engine = ResearchEngine(storage, _DummyKnowledge(), _UnavailableSearch())
            engine.SEARCH_RETRY_SECONDS = 60

            task_id = engine.enqueue("pharmacology review", reason="regression")
            claimed = engine._claim(task_id)
            self.assertIsNotNone(claimed)

            result = engine._execute(task_id, "pharmacology review")
            self.assertEqual(result["status"], "retry_queued")
            self.assertEqual(result["retry_after_seconds"], 60)

            with storage.connection() as conn:
                row = conn.execute(
                    "SELECT status,attempts,last_error FROM research_tasks WHERE id=?",
                    (task_id,),
                ).fetchone()
            self.assertEqual(row["status"], "queued")
            self.assertEqual(int(row["attempts"]), 1)
            self.assertIn("временно недоступна", str(row["last_error"]))

            self.assertIsNone(engine._claim(task_id))

            with storage.connection() as conn:
                conn.execute(
                    "UPDATE research_tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                    (task_id,),
                )
            self.assertIsNotNone(engine._claim(task_id))

    def test_live_search_outage_is_retry_queued_not_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(Path(temp) / "leshy.sqlite3")
            engine = ResearchEngine(storage, _DummyKnowledge(), _UnavailableSearch())

            result = engine.live_query("site:royalsocietypublishing.org pharmacology")
            self.assertEqual(result["status"], "retry_queued")

            with storage.connection() as conn:
                row = conn.execute(
                    "SELECT status,attempts,last_error FROM research_tasks WHERE id=?",
                    (int(result["task_id"]),),
                ).fetchone()
            self.assertEqual(row["status"], "queued")
            self.assertGreaterEqual(int(row["attempts"]), 1)
            self.assertTrue(str(row["last_error"]))


if __name__ == "__main__":
    unittest.main()
