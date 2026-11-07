from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from leshy.adaptive_internet_policy import (
    SIX_MONTHS_SECONDS,
    adaptive_research_reason,
    is_explicit_web_request,
    knowledge_is_current,
    ttl_for_context,
)
from leshy.knowledge import KnowledgeBase
from leshy.storage import Storage


class AdaptiveInternetPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def _record(self, *, days=0, status="trusted", dynamic=False, ttl=None):
        stamp = (self.now - timedelta(days=days)).isoformat()
        return {
            "status": status,
            "topic": "Python release",
            "claim": "Python release documentation is available.",
            "source_url": "https://docs.python.org/3/",
            "data_class": "dynamic" if dynamic else "static",
            "captured_at": stamp,
            "checked_at": stamp if dynamic else None,
            "ttl_seconds": ttl,
        }

    def test_default_six_months_for_normal_factual_information(self):
        self.assertEqual(ttl_for_context("Расскажи про архитектуру процессора"), SIX_MONTHS_SECONDS)

    def test_contextual_expiration(self):
        self.assertLess(ttl_for_context("Курс валют сегодня"), ttl_for_context("Какая последняя версия Python?"))
        self.assertLess(ttl_for_context("Какая погода завтра?"), ttl_for_context("Расскажи про архитектуру процессора"))
        self.assertLess(ttl_for_context("CVE-2026-119977"), SIX_MONTHS_SECONDS)

    def test_local_os_time_used_for_knowledge_age(self):
        record = self._record(days=179)
        self.assertTrue(knowledge_is_current(record, "Что такое архитектура процессора?", now=self.now))
        record = self._record(days=181)
        self.assertFalse(knowledge_is_current(record, "Что такое архитектура процессора?", now=self.now))

    def test_dynamic_record_uses_strictest_ttl(self):
        record = self._record(dynamic=True, ttl=3600)
        record["checked_at"] = (self.now - timedelta(minutes=35)).isoformat()
        self.assertFalse(knowledge_is_current(record, "Какая погода?", now=self.now))
        self.assertTrue(knowledge_is_current(record, "Последняя версия Python?", now=self.now))

    def test_forced_search_overrides_recent_cached_fact(self):
        recent = self._record()
        self.assertTrue(is_explicit_web_request("/search latest Python release"))
        for question in ("/search latest Python release", "Проверь в интернете последнюю версию Python"):
            self.assertEqual(adaptive_research_reason(question, [recent], []), "явный запрос интернет-поиска")

    def test_recent_trusted_dynamic_does_not_call_internet_every_time(self):
        recent = self._record()
        self.assertIsNone(adaptive_research_reason("Какая последняя версия Python?", [recent], []))

    def test_stale_trusted_requires_refresh(self):
        stale = {
            "status": "trusted",
            "captured_at": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
            "data_class": "static",
        }
        self.assertEqual(
            adaptive_research_reason("Расскажи про архитектуру процессора", [stale], []),
            "срок актуальности проверенных знаний истёк",
        )

    def test_only_candidate_requires_web(self):
        self.assertEqual(
            adaptive_research_reason("Расскажи про архитектуру процессора", [], [self._record(status="candidate")]),
            "есть только непроверенные сведения",
        )

    def test_common_and_creative_requests_stay_fast(self):
        for question in (
            "Напиши новый текст для поздравления",
            "Что такое закон Ома?",
            "Придумай историю про лес",
            "Почему небо голубое?",
        ):
            with self.subTest(question=question):
                self.assertIsNone(adaptive_research_reason(question, [], []))

    def test_unseen_recent_security_identifier_requires_web(self):
        self.assertEqual(
            adaptive_research_reason("Что известно про CVE-2026-119977?", [], []),
            "свежая проверка бюллетеня безопасности",
        )


class KnowledgeRelevanceRegressionTests(unittest.TestCase):
    def test_unrelated_high_confidence_fact_must_not_mask_missing_information(self):
        with TemporaryDirectory() as tmp:
            store = Storage(Path(tmp) / "brain.sqlite3")
            knowledge = KnowledgeBase(store)
            knowledge.add_candidate(
                topic="Python 3.14",
                claim="Python 3.14 includes interpreter improvements according to documentation.",
                source_url="https://docs.python.org/3.14/whatsnew/3.14.html",
            )
            knowledge.verify_topic("Python 3.14")
            self.assertEqual(len(knowledge.search("Что такое Python 3.14?", trusted_only=True)), 1)
            self.assertEqual(knowledge.search("Что такое Python 3.15?", trusted_only=True), [])
            self.assertEqual(knowledge.search("Что известно про CVE-2026-119977?", trusted_only=True), [])
            self.assertEqual(knowledge.search("Что такое архитектура процессора?", trusted_only=True), [])


if __name__ == "__main__":
    unittest.main()
