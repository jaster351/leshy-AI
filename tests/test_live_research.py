from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leshy.app import LeshyApp
from leshy.knowledge import KnowledgeBase
from leshy.research import ResearchEngine, SearchResult
from leshy.research_policy import research_reason
from leshy.storage import Storage


class FakeSearch:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def search(self, query: str, limit: int = 5):
        self.calls += 1
        return self.results[:limit]


class LiveResearchTests(unittest.TestCase):
    def test_dynamic_query_requires_internet_even_with_local_knowledge(self):
        reason = research_reason("Какая погода сейчас в Москве?", trusted_count=10, candidate_count=5)
        self.assertEqual(reason, "требуются актуальные данные")

    def test_fresh_video_question_requires_internet_even_with_local_knowledge(self):
        reason = research_reason(
            "Какой новый ролик вышел у Марка как он называется",
            trusted_count=10,
            candidate_count=5,
        )
        self.assertEqual(reason, "требуются актуальные данные")

    def test_explicit_web_request_requires_internet(self):
        reason = research_reason("Проверь в интернете последнюю версию Python", trusted_count=20, candidate_count=10)
        self.assertIsNotNone(reason)

    def test_knowledge_gap_requires_internet_for_information_question(self):
        reason = research_reason("Что такое WebGPU?", trusted_count=0, candidate_count=0)
        self.assertEqual(reason, "пробел в локальной базе знаний")

    def test_small_talk_does_not_force_internet(self):
        self.assertIsNone(research_reason("Привет", trusted_count=0, candidate_count=0))

    def test_model_refusal_is_treated_as_knowledge_gap(self):
        answer = (
            "Информации о новых роликах Марка в моих данных нет. "
            "У меня нет доступа к интернету или текущим новостям."
        )
        self.assertTrue(LeshyApp._answer_needs_research(answer))

    def test_knowledge_counts_expose_explicit_status_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "brain.db")
            knowledge = KnowledgeBase(storage)
            empty = knowledge.counts()
            self.assertEqual(empty["knowledge_total"], 0)
            self.assertEqual(empty["candidate"], 0)
            self.assertEqual(empty["trusted"], 0)
            self.assertEqual(empty["total"], empty["knowledge_total"])

            knowledge.add_candidate(
                topic="WebGPU",
                claim="WebGPU exposes modern GPU capabilities to web applications through a portable API.",
                source_url="https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API",
                source_title="WebGPU API",
            )
            counts = knowledge.counts()
            self.assertEqual(counts["knowledge_total"], 1)
            self.assertEqual(counts["candidate"], 1)
            self.assertEqual(counts["trusted"], 0)
            self.assertEqual(counts["total"], counts["knowledge_total"])

    def test_live_search_immediately_increases_knowledge_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "brain.db")
            knowledge = KnowledgeBase(storage)
            search = FakeSearch([
                SearchResult(
                    "Python 3.14 docs",
                    "https://docs.python.org/3/whatsnew/3.14.html",
                    "Python 3.14 introduces several language and runtime improvements described in the official documentation.",
                ),
                SearchResult(
                    "Python release",
                    "https://www.python.org/downloads/",
                    "The Python downloads page lists currently available releases and their publication information.",
                ),
            ])
            engine = ResearchEngine(storage, knowledge, search)

            before = knowledge.counts()
            result = engine.live_query("актуальная версия Python", reason="test")
            after = knowledge.counts()

            self.assertEqual(search.calls, 1)
            self.assertEqual(result["status"], "queued_verification")
            self.assertGreater(result["accepted_claims"], 0)
            self.assertGreater(after["knowledge_total"], before["knowledge_total"])
            self.assertGreater(after["candidate"], before["candidate"])
            with storage.connection() as conn:
                task = conn.execute("SELECT status FROM research_tasks WHERE id=?", (result["task_id"],)).fetchone()
            self.assertEqual(task["status"], "queued")


if __name__ == "__main__":
    unittest.main()
