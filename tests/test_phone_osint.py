from types import SimpleNamespace

from leshy.phone_osint import PhoneOsintService, extract_phone_candidate, is_phone_osint_request
from leshy.research import SearchResult


class FakeSearch:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def search(self, query: str, limit: int = 5):
        self.queries.append(query)
        return list(self.rows)[:limit]


def _app(rows):
    return SimpleNamespace(research=SimpleNamespace(search_provider=FakeSearch(rows)))


def test_phone_request_detection_and_extraction():
    text = "Кто звонил с +1 (415) 555-2671?"
    assert is_phone_osint_request(text)
    assert extract_phone_candidate(text) == "+1 (415) 555-2671"
    assert not is_phone_osint_request("Позвони мне на +1 415 555 2671")


def test_multiple_independent_fraud_reports_raise_risk():
    rows = [
        SearchResult("Scam report", "https://fraud-one.example/phone", "fraud scam robocall"),
        SearchResult("Мошенники звонят", "https://fraud-two.example/report", "мошенники и обман"),
        SearchResult("Vishing alert", "https://fraud-three.example/item", "vishing impersonation"),
        SearchResult("Spam caller", "https://fraud-four.example/lookup", "spam scam caller"),
    ]
    result = PhoneOsintService(_app(rows)).analyze("кто звонил +1 415 555 2671")
    assert result["risk_score"] >= 70
    assert result["confidence"] >= 0.7
    assert len(result["evidence"]) == 4
    assert all(item["kind"] == "fraud-report" for item in result["evidence"])


def test_public_business_listing_is_not_marked_as_fraud():
    rows = [
        SearchResult(
            "Example Clinic — official contact",
            "https://clinic.example/contacts",
            "official company contact customer service",
        )
    ]
    result = PhoneOsintService(_app(rows)).analyze("что за номер +1 415 555 2671")
    assert result["risk_score"] < 20
    assert result["evidence"][0]["kind"] == "business"
    assert "организац" in result["verdict"]


def test_leak_and_private_database_results_are_excluded():
    rows = [
        SearchResult(
            "Слитая база телефонов и паспортов",
            "https://leaks.example/item",
            "утечка адрес проживания паспорт родственники",
        )
    ]
    result = PhoneOsintService(_app(rows)).analyze("проверь номер +1 415 555 2671")
    assert result["evidence"] == []
    assert result["verdict"] == "данных недостаточно"


def test_answer_always_warns_about_caller_id_spoofing():
    rows = [SearchResult("Spam caller", "https://spam.example/x", "spam robocall")]
    result = PhoneOsintService(_app(rows)).analyze("/phone +1 415 555 2671")
    answer = PhoneOsintService.format_answer(result)
    assert "Caller ID" in answer
    assert "подменён" in answer
