from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.knowledge import KnowledgeBase, source_weight
from leshy.research_policy import research_reason
from leshy.scientific_learning import (
    SCIENTIFIC_MATERIAL_TARGET,
    is_academic_url,
    news_query,
    scientific_query,
)
from leshy.storage import Storage


class AutonomousLearningTests(unittest.TestCase):
    def test_scientific_goal_is_2000_materials(self):
        self.assertEqual(SCIENTIFIC_MATERIAL_TARGET, 2000)

    def test_science_curriculum_targets_academic_domains(self):
        item = scientific_query(0)
        self.assertTrue(item.domain)
        self.assertIn("site:" + item.domain, item.query)
        self.assertIn("review", item.query)

    def test_academic_source_detection(self):
        self.assertTrue(is_academic_url("https://arxiv.org/abs/1234.5678"))
        self.assertTrue(is_academic_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC1/"))
        self.assertFalse(is_academic_url("https://example.com/post"))

    def test_news_query_is_freshness_oriented(self):
        query = news_query(0)
        self.assertIn("latest news research", query)

    def test_candidate_knowledge_does_not_suppress_internet(self):
        reason = research_reason(
            "Расскажи, как работают трансформеры?",
            trusted_count=0,
            candidate_count=5,
        )
        self.assertEqual(reason, "есть только непроверенные candidate-знания")

    def test_academic_source_is_high_quality_but_not_single_source_trusted(self):
        self.assertGreater(source_weight("https://arxiv.org/abs/1"), 0.80)
        self.assertLess(source_weight("https://arxiv.org/abs/1"), 0.90)
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "leshy.sqlite3")
            knowledge = KnowledgeBase(storage, min_trusted_confidence=0.80)
            claim = "Transformers use attention mechanisms to combine information across token positions."
            knowledge.add_candidate(
                topic="transformers",
                claim=claim,
                source_url="https://arxiv.org/abs/1706.03762",
                source_title="Attention Is All You Need",
                source_type="scientific",
            )
            first = knowledge.verify_topic("transformers")
            self.assertEqual(first["trusted"], 0)
            self.assertEqual(first["candidate"], 1)
            knowledge.add_candidate(
                topic="transformers",
                claim=claim,
                source_url="https://jmlr.org/papers/example.html",
                source_title="Independent confirmation",
                source_type="scientific",
            )
            second = knowledge.verify_topic("transformers")
            self.assertEqual(second["trusted"], 1)


if __name__ == "__main__":
    unittest.main()
