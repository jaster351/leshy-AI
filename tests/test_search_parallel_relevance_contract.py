"""Regression contracts for relevance filtering in the parallel web search.

No network access or model inference is required.
"""
from leshy.research import SearchResult
from leshy.search_parallel import (
    _focused_rss_query,
    _relevant_search_results,
)


def _hit(title: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url="https://example.org/article", snippet=snippet)


def test_unrelated_valid_url_is_not_a_success():
    unrelated = _hit("Football scores", "Latest match results")
    assert _relevant_search_results("Когда выйдет GTA 6?", [unrelated]) == []


def test_release_number_must_match():
    older = _hit("GTA 5 release date")
    target = _hit("Grand Theft Auto VI release date")
    assert _relevant_search_results("Когда выйдет GTA 6?", [older, target]) == [target]


def test_relevant_title_or_snippet_is_preserved():
    hit = _hit("Official announcement", "GTA 6 release date")
    assert _relevant_search_results("Когда выйдет GTA 6?", [hit]) == [hit]


def test_no_topic_keeps_results_instead_of_failing_closed():
    hit = _hit("General news")
    assert _relevant_search_results("Что нового сегодня?", [hit]) == [hit]


def test_rss_focus_removes_conversational_filler():
    focused = _focused_rss_query("Расскажи пожалуйста когда выйдет GTA 6")
    assert "gta 6" in focused
    assert "расскажи" not in focused
    assert "дата выхода" in focused


def test_site_restriction_is_not_rewritten():
    query = "Расскажи про GTA 6 site:rockstargames.com"
    assert _focused_rss_query(query) == query
