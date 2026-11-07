import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from leshy.phone_footprint import AuthorizedTagFileProvider, PhonePublicFootprint
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
        self.assertTrue(is_phone_osint_request("как подписан номер +1 415 555 2671"))
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

    def test_public_service_hit_requires_number_on_matching_service_domain(self):
        rows = [
            SearchResult("Public Telegram mention", "https://t.me/example", "Контакт +1 (415) 555-2671"),
            SearchResult("Unrelated profile", "https://instagram.com/example", "another person"),
        ]
        footprint = PhonePublicFootprint(FakeSearch(rows)).analyze(
            e164="+14155552671",
            international="+1 415-555-2671",
            national="(415) 555-2671",
        )
        telegram = next(item for item in footprint["services"] if item["service"] == "telegram")
        instagram = next(item for item in footprint["services"] if item["service"] == "instagram")
        self.assertEqual(telegram["status"], "public_match")
        self.assertGreaterEqual(telegram["confidence"], 0.7)
        self.assertEqual(instagram["status"], "no_public_evidence")

    def test_caller_tags_require_authorized_source_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            result = AuthorizedTagFileProvider().lookup("+14155552671")
        self.assertEqual(result["status"], "requires_authorized_source")
        self.assertEqual(result["tags"], [])

    def test_authorized_tag_file_is_supported_without_network_scraping(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tags.json"
            path.write_text('{"+14155552671": ["Мастер Алексей", "Сервис"]}', encoding="utf-8")
            result = AuthorizedTagFileProvider(str(path)).lookup("+14155552671")
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["tags"], ["Мастер Алексей", "Сервис"])

    def test_formatted_answer_reports_public_service_and_registration_limit(self):
        rows = [SearchResult("Telegram contact", "https://t.me/example", "Телефон +1 415 555 2671")]
        with patch.dict(os.environ, {}, clear=True):
            result = PhoneOsintService(_app(rows)).analyze("где зарегистрирован номер +1 415 555 2671")
        answer = PhoneOsintService.format_answer(result)
        self.assertIn("Telegram", answer)
        self.assertIn("не доказывает регистрацию", answer)
        self.assertIn("скрейпинг", answer)

    def test_answer_always_warns_about_caller_id_spoofing(self):
        rows = [SearchResult("Spam caller", "https://spam.example/x", "spam robocall")]
        result = PhoneOsintService(_app(rows)).analyze("/phone +1 415 555 2671")
        answer = PhoneOsintService.format_answer(result)
        self.assertIn("Caller ID", answer)
        self.assertIn("подменён", answer)


if __name__ == "__main__":
    unittest.main()
