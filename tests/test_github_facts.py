from __future__ import annotations

import unittest

from leshy.github_facts import (
    GitHubEvidenceUnavailable, GitHubRepositoryEvidence,
    is_leshy_repository_audit_request,
)


class FakeGithubAPI:
    def __init__(self, *, broken: bool = False):
        self.calls = []
        self.broken = broken

    def __call__(self, path: str):
        self.calls.append(path)
        if self.broken:
            raise GitHubEvidenceUnavailable("network_failed")
        if path.startswith("/issues?state=all"):
            return [
                {"state": "open", "user": {"login": "jaster351"}},
                {"state": "closed", "user": {"login": "jaster351"}},
                {"state": "open", "pull_request": {}, "user": {"login": "jaster351"}},
            ]
        if path.startswith("/issues/comments?"):
            return [
                {"user": {"login": "jaster351"}},
                {"user": {"login": "jaster351"}},
            ]
        if path.startswith("/pulls/comments?"):
            return [{"user": {"login": "jaster351"}}]
        raise AssertionError("Unknown GitHub route: " + path)


class GitHubFactsTests(unittest.TestCase):
    def test_recognizes_repository_author_questions_but_not_other_requests(self):
        self.assertTrue(is_leshy_repository_audit_request(
            "Леший, сколько Issues и кто их авторы в репозитории?"
        ))
        self.assertTrue(is_leshy_repository_audit_request(
            "Есть ли чужие комментарии в репозитории Лешего?"
        ))
        self.assertTrue(is_leshy_repository_audit_request(
            "Проверь число открытых Issues в jaster351/leshy-AI на GitHub"
        ))
        self.assertFalse(is_leshy_repository_audit_request(
            "Что нового в GitHub Copilot?"
        ))
        self.assertFalse(is_leshy_repository_audit_request(
            "Сколько Issues у https://github.com/example/project?"
        ))

    def test_short_followup_reuses_only_recent_explicit_repository_context(self):
        prior = [{"role": "assistant", "content": "Тут вопросы по GitHub"},
                 {"role": "user", "content": "Леший, кто авторы Issues в твоём репозитории?"}]
        self.assertTrue(is_leshy_repository_audit_request("А что написал не ты?", prior))
        self.assertFalse(is_leshy_repository_audit_request("А что написал не ты?", []))
        evidence = GitHubRepositoryEvidence(FakeGithubAPI())
        result = evidence.answer("А что написал не ты?", prior)
        self.assertTrue(result["verified"])
        self.assertIn("Авторы Issues: jaster351", result["answer"])

    def test_author_names_and_counts_only_from_api(self):
        api = FakeGithubAPI()
        evidence = GitHubRepositoryEvidence(api)
        result = evidence.answer("Кто авторы issues в репозитории Лешего?")
        self.assertTrue(result["verified"])
        self.assertIn("2 всего, 1 открыто, 1 закрыто", result["answer"])
        self.assertIn("jaster351 — 2", result["answer"])
        self.assertIn("Pull Requests: 1", result["answer"])
        self.assertNotIn("michal", result["answer"])
        self.assertIn("других аккаунтов", result["answer"])
        self.assertNotIn("непроверенные", result["answer"])
        self.assertTrue(evidence.receipts.verify(result["receipt_id"])["verified"])
        self.assertEqual(len(api.calls), 3)

    def test_foreign_user_is_reported_when_api_has_evidence(self):
        api = FakeGithubAPI()
        old_call = api.__call__

        def data(path):
            if path.startswith("/issues/comments?"):
                return [{"user": {"login": "external-reviewer"}}]
            return old_call(path)
        evidence = GitHubRepositoryEvidence(data)
        result = evidence.answer("Есть чужие комментарии в репозитории Лешего?")
        self.assertIn("external-reviewer", result["answer"])
        self.assertIn("Другие GitHub-аккаунты", result["answer"])

    def test_failing_api_cannot_invent_users_or_counts(self):
        evidence = GitHubRepositoryEvidence(FakeGithubAPI(broken=True))
        result = evidence.answer("Сколько Issues в репозитории Лешего?")
        self.assertFalse(result["verified"])
        self.assertEqual(result["sources"], [])
        self.assertIn("Не удалось получить полные данные", result["answer"])
        self.assertNotIn("jaster351 — ", result["answer"])
        self.assertTrue(evidence.receipts.verify(result["receipt_id"])["verified"])

    def test_incomplete_pagination_is_not_treated_as_complete(self):
        def endless(path):
            return [{"state": "open", "user": {"login": "jaster351"}}] * 100
        evidence = GitHubRepositoryEvidence(endless)
        result = evidence.answer("Сколько Issues в репозитории Лешего?")
        self.assertFalse(result["verified"])
        self.assertEqual(result["sources"], [])

    def test_does_not_make_repeat_api_calls_within_fresh_ttl(self):
        api = FakeGithubAPI()
        evidence = GitHubRepositoryEvidence(api)
        one = evidence.answer("Сколько Issues в репозитории Лешего?")
        two = evidence.answer("Кто авторы Issues в репозитории Лешего?")
        self.assertTrue(one["verified"] and two["verified"])
        self.assertEqual(len(api.calls), 3)
        self.assertIn("локального кэша", two["answer"])

    def test_deleted_account_blocks_false_all_owner_claim(self):
        def get(path):
            if path.startswith("/issues?state=all"):
                return [{"state": "open", "user": None}]
            return []
        evidence = GitHubRepositoryEvidence(get)
        result = evidence.answer("Все авторы Issues в репозитории Лешего?")
        self.assertTrue(result["verified"])
        self.assertIn("автор не определён", result["answer"])
        self.assertNotIn("других аккаунтов, кроме", result["answer"])


if __name__ == "__main__":
    unittest.main()
