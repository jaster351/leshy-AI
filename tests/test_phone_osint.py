import unittest
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


class PhoneOsintTests(unittest.TestCase):
    def test_phone_request_detection_and_extraction(self):
        text = "Кто звонил с +1 (415) 555-2671?"
        self.assertTrue(is_phone_osint_request(text))
        self.assertEqual(extract_phone_candidate(text), "+1 (415) 555-2671")
        self.assertFalse(is_phone_osint_request("Позвони мне на +1 415 555 2671"))

    def test_multiple_independent_fraud_reports_raise_risk(self):
        rows = [
            SearchResult("Scam report", "https://fraud-one.example/phone", "fraud scam robocall"),
            SearchResult("Мошенники звонят", "https://fraud-two.example/report", "мошенники и обман"),
            SearchResult("Vishing alert", "https://fraud-three.example/item", "vishing impersonation"),
            SearchResult("Spam caller", "https://fraud-four.example/lookup", "spam scam caller"),
        ]
        result = PhoneOsintService(_app(rows)).analyze("кто звонил +1 415 555 2671")
        self.assertGreaterEqual(result["risk_score"], 70)
        self.assertGreaterEqual(result["confidence"], 0.7)
        self.assertEqual(len(result["evidence"]), 4)
        self.assertTrue(all(item["kind"] == "fraud-report" for item in result["evidence"]))

    def test_public_business_listing_is_not_marked_as_fraud(self):
        rows = [
            SearchResult(
                "Example Clinic — official contact",
                "https://clinic.example/contacts",
                "official company contact customer service",
            )
        ]
        result = PhoneOsintService(_app(rows)).analyze("что за номер +1 415 555 2671")
        self.assertLess(result["risk_score"], 20)
        self.assertEqual(result["evidence"][0]["kind"], "business")
        self.assertIn("организац", result["verdict"])

    def test_leak_and_private_database_results_are_excluded(self):
        rows = [
            SearchResult(
                "Слитая база телефонов и паспортов",
                "https://leaks.example/item",
                "утечка адрес проживания паспорт родственники",
            )
        ]
        result = PhoneOsintService(_app(rows)).analyze("проверь номер +1 415 555 2671")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["verdict"], "данных недостаточно")

    def test_answer_always_warns_about_caller_id_spoofing(self):
        rows = [SearchResult("Spam caller", "https://spam.example/x", "spam robocall")]
        result = PhoneOsintService(_app(rows)).analyze("/phone +1 415 555 2671")
        answer = PhoneOsintService.format_answer(result)
        self.assertIn("Caller ID", answer)
        self.assertIn("подменён", answer)


if __name__ == "__main__":
    unittest.main()
