from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase

from leshy.source_reading import read_supplied_source, supplied_public_url
from leshy.verified_clock import clock_answer, current_clock_context, is_clock_request
from leshy.research import ResearchEngine, SearchResult


class ClockSourceRegressionTests(TestCase):
    def test_clock_recognizes_normal_speech(self):
        for phrase in ("который час?", "Который сейчас час", "сколько времени?",
                       "какой сегодня день?", "какое сегодня число?",
                       "какая сегодня дата?"):
            with self.subTest(phrase=phrase):
                self.assertTrue(is_clock_request(phrase))
                self.assertIn("Местное время компьютера:", clock_answer(phrase))

    def test_clock_does_not_intercept_unrelated_queries(self):
        for phrase in ("Какой год выпуска машины?", "Какая погода в Челябинске?",
                       "сколько лет персонажу"):
            self.assertFalse(is_clock_request(phrase))

    def test_clock_context_uses_current_machine_calendar(self):
        text = current_clock_context()
        self.assertIn(str(datetime.now().year), text)
        self.assertIn("UTC", text)
        self.assertIn("не подтверждает истинность", text)


class _FakeLearning:
    def __init__(self, enabled):
        self.enabled = enabled

    def is_enabled(self):
        return self.enabled


class _FakeFetcher:
    def fetch_text(self, url, timeout):
        return (
            "В этом материале перечислены исправления для уязвимости CVE-2026-119977. "
            "Описание содержит проверяемые технические сведения, которые необходимо "
            "сопоставить с данными производителя и независимыми источниками."
        )


class _FakeKnowledge:
    def __init__(self):
        self.added = []

    def add_candidate(self, **kwargs):
        self.added.append(kwargs)
        return len(self.added)


class _FakeLearningWorker:
    def __init__(self):
        self.tasks = []

    def queue_topic(self, query, reason):
        self.tasks.append((query, reason))
        return 1


class _FakeResearch:
    def __init__(self):
        self.fetcher = _FakeFetcher()

    def _extract_candidate_claims(self, text):
        return [text[:300]]


def _fake_app(enabled):
    return SimpleNamespace(
        learning=_FakeLearning(enabled),
        research=_FakeResearch(),
        knowledge=_FakeKnowledge(),
        self_learning=_FakeLearningWorker(),
    )


class WebSourceLearningTests(TestCase):
    def test_no_url_means_no_fetch(self):
        self.assertIsNone(supplied_public_url("Привет, Леший"))
        app = _fake_app(True)
        self.assertEqual(read_supplied_source(app, "Привет, Леший"), ("", []))
        self.assertEqual(app.knowledge.added, [])

    def test_read_with_learning_off_does_not_persist(self):
        app = _fake_app(False)
        note, links = read_supplied_source(app, "Переведи и объясни https://example.org/article")
        self.assertIn("НЕДОВЕРЕННОГО ТЕКСТА", note)
        self.assertEqual(len(links), 1)
        self.assertEqual(app.knowledge.added, [])
        self.assertEqual(app.self_learning.tasks, [])

    def test_learning_on_creates_only_candidates(self):
        app = _fake_app(True)
        note, links = read_supplied_source(app, "Изучи https://example.org/CVE-2026-119977")
        self.assertEqual(len(links), 1)
        self.assertEqual(len(app.knowledge.added), 1)
        self.assertEqual(app.knowledge.added[0]["source_type"], "user-read-web")
        self.assertEqual(app.knowledge.added[0]["data_class"], "dynamic")
        self.assertEqual(app.knowledge.added[0]["ttl_seconds"], 86_400)
        self.assertEqual(len(app.self_learning.tasks), 1)
        self.assertIn("непроверенных утверждений", note)


class _FakeStorage:
    def __init__(self):
        self.events = []

    def audit(self, name, data):
        self.events.append((name, data))


class _FakeSearchProvider:
    def search(self, query, limit=5):
        return [SearchResult("Пример", "https://example.org/item",
                             "Проверяемая заметка с достаточным объёмом текста для кандидата.")]


class LiveSearchLearningSwitchTests(TestCase):
    def test_search_without_autolearning_does_not_create_candidates_or_tasks(self):
        storage = _FakeStorage()
        knowledge = _FakeKnowledge()
        engine = ResearchEngine(storage, knowledge, _FakeSearchProvider())
        engine.auto_learning_enabled = lambda: False
        result = engine.live_query("Какие новости?", limit=2)
        self.assertEqual(result["status"], "search_complete")
        self.assertEqual(len(result["results"]), 1)
        self.assertFalse(result["verification_queued"])
        self.assertEqual(knowledge.added, [])
        self.assertNotIn("research.queued", [event for event, _ in storage.events])
