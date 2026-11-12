"""Проверяемые сведения о каноническом GitHub-репозитории Лешего.

Ни одно количество или имя автора не генерируется LLM. Источник — только
успешные и полностью прочитанные ответы GitHub REST API. Данные об авторах
Issue, PR и комментариев не следует путать с реальными личностями.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import re
import threading
import time
from typing import Any, Callable
from urllib import error, request

from .verified_tool_receipts import VerifiedToolReceipts


REPO = "jaster351/leshy-AI"
OWNER = "jaster351"
_API = "https://api.github.com/repos/" + REPO
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Leshy-GitHub-Facts/1",
    "X-GitHub-Api-Version": "2022-11-28",
}
_MAX_BODY = 1024 * 1024
_MAX_PAGES = 8
_PAGE_SIZE = 100
_OWNER_WORDS = re.compile(
    r"(?:леш(?:ий|его|ему|ем)|leshy|jaster351|тво(?:ём|ем|его|ей|и)|"
    r"мо(?:ём|ем|его|ей|и)|наш(?:ем|ём|его)|его\s+репозитори)",
    re.IGNORECASE,
)
_REPO_WORDS = re.compile(r"(?:github|гитхаб|репозитор|исходник|issues|issue|pull requests?|pr\b)", re.IGNORECASE)
_AUDIT_WORDS = re.compile(
    r"(?:автор|пользоват|аккаунт|уч[её]тн|кто\s+(?:писал|создал|добавил)|"
    r"вопрос|issues?|коммент|сообщени|обсужден|посторон|чужи|"
    r"количеств|сколько|открыт|закрыт|46\s+штук|pull request|запрос\s+на\s+слиян)",
    re.IGNORECASE,
)
_GITHUB_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?$")


class GitHubEvidenceUnavailable(RuntimeError):
    """Полный фактический снимок недоступен: числа нельзя додумывать."""


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def is_leshy_repository_audit_request(message: str) -> bool:
    """Узкое правило, не перехватывающее обычные беседы о GitHub и коде."""
    text = " ".join(str(message or "").strip().split())[:1200]
    if not text or not _AUDIT_WORDS.search(text):
        return False
    # Если пользователь назвал чужой репозиторий, не подменять его Лешим.
    foreign = re.findall(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", text, flags=re.IGNORECASE)
    if any(name.lower().removesuffix(".git") != REPO.lower() for name in foreign):
        return False
    foreign_short = re.findall(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", text)
    if any(
        name.lower() not in {REPO.lower(), "issues/comments", "pulls/comments"}
        for name in foreign_short
        if name.lower().split("/")[0] not in {"http:", "https:"}
        and (name.lower().split("/")[0] != "github.com")
        and name.lower().count("/") == 1
        and any(term in text.lower() for term in ("repo", "репозитор", "github.com"))
        and name.lower().split("/")[0] not in {"src", "docs", "v1"}
    ):
        # Не подменять точно указанный другой owner/repo. Предикат намеренно узкий:
        # строки вида src/leshy или docs/... не считаются адресом GitHub.
        if "github.com/" not in text.lower() and "jaster351/leshy-AI".lower() not in text.lower():
            return False
    return bool(_OWNER_WORDS.search(text) and _REPO_WORDS.search(text))


def _login(record: Any) -> str:
    if not isinstance(record, dict):
        return "<unknown>"
    user = record.get("user")
    if not isinstance(user, dict):
        return "<unknown>"
    name = user.get("login")
    return str(name) if isinstance(name, str) and _GITHUB_USER.fullmatch(name) else "<unknown>"


def _counts(records: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(_login(item) for item in records).items()))


def _render_users(label: str, values: dict[str, int]) -> str:
    if not values:
        return f"{label}: записей нет."
    return f"{label}: " + ", ".join(f"{user} — {count}" for user, count in values.items()) + "."


def _http_json(path: str) -> list[dict]:
    if not path.startswith("/") or "://" in path or "\\" in path:
        raise GitHubEvidenceUnavailable("Некорректный маршрут GitHub API")
    url = _API + path
    req = request.Request(url, headers=_HEADERS, method="GET")
    opener = request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=8) as response:
            if response.status != 200:
                raise GitHubEvidenceUnavailable(f"GitHub HTTP {response.status}")
            data = response.read(_MAX_BODY + 1)
    except (OSError, TimeoutError, error.HTTPError, error.URLError) as exc:
        raise GitHubEvidenceUnavailable(type(exc).__name__) from None
    if len(data) > _MAX_BODY:
        raise GitHubEvidenceUnavailable("Слишком большой ответ GitHub")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise GitHubEvidenceUnavailable("Некорректный JSON GitHub") from None
    if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
        raise GitHubEvidenceUnavailable("GitHub не вернул список")
    return result


def _pages(path: str, get_json: Callable[[str], list[dict]]) -> list[dict]:
    all_rows: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        mark = "&" if "?" in path else "?"
        chunk = get_json(f"{path}{mark}per_page={_PAGE_SIZE}&page={page}")
        if not isinstance(chunk, list) or not all(isinstance(r, dict) for r in chunk):
            raise GitHubEvidenceUnavailable("Неполный или неверный ответ GitHub")
        all_rows.extend(chunk)
        if len(chunk) < _PAGE_SIZE:
            return all_rows
    raise GitHubEvidenceUnavailable("Не все страницы GitHub обработаны")


class GitHubRepositoryEvidence:
    """Кешируемое чтение только публичного GitHub API, без GitHub token."""

    def __init__(self, fetch_json: Callable[[str], list[dict]] | None = None,
                 *, ttl_seconds: int = 300):
        self.fetch_json = fetch_json or _http_json
        self.ttl_seconds = max(1, min(int(ttl_seconds), 1800))
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self.receipts = VerifiedToolReceipts()

    def _snapshot(self) -> tuple[dict[str, Any], bool]:
        with self._lock:
            if self._cache is not None and time.monotonic() - self._cache_at < self.ttl_seconds:
                return self._cache, True
            # REST /issues включает PR — обязательно разделяем по pull_request.
            all_issues = _pages("/issues?state=all", self.fetch_json)
            issues = [r for r in all_issues if "pull_request" not in r]
            prs = [r for r in all_issues if "pull_request" in r]
            comments = _pages("/issues/comments", self.fetch_json)
            inline_reviews = _pages("/pulls/comments", self.fetch_json)
            issue_count = sum(bool(str(r.get("state") or "").lower() == "open") for r in issues)
            snapshot = {
                "issues": {"all": len(issues), "open": issue_count,
                           "closed": len(issues) - issue_count, "authors": _counts(issues)},
                "prs": {"all": len(prs), "authors": _counts(prs)},
                "issue_comments": {"all": len(comments), "authors": _counts(comments)},
                "inline_review_comments": {"all": len(inline_reviews), "authors": _counts(inline_reviews)},
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self._cache = snapshot
            self._cache_at = time.monotonic()
            return snapshot, False

    def answer(self, message: str) -> dict[str, Any] | None:
        if not is_leshy_repository_audit_request(message):
            return None
        sources = [
            {"title": "GitHub Issues · Leshy", "url": "https://github.com/" + REPO + "/issues"},
            {"title": "GitHub Pull Requests · Leshy", "url": "https://github.com/" + REPO + "/pulls"},
        ]
        try:
            snapshot, cached = self._snapshot()
        except GitHubEvidenceUnavailable as exc:
            receipt = self.receipts.record("github.repository_audit", "error",
                                           {"repo": REPO, "error_type": str(exc)[:80]})
            return {
                "answer": "Не удалось получить полные данные из GitHub API. "
                          "Сейчас я не могу подтвердить количество вопросов, "
                          "имена авторов или отсутствие чужих комментариев. "
                          "Называть числа и пользователей без проверки было бы выдумкой.",
                "sources": [],
                "verified": False,
                "receipt_id": receipt.receipt_id,
            }
        receipt = self.receipts.record("github.repository_audit", "success", snapshot)
        authors = snapshot["issues"]["authors"]
        all_authors = set().union(authors, snapshot["prs"]["authors"],
                                  snapshot["issue_comments"]["authors"],
                                  snapshot["inline_review_comments"]["authors"])
        outsiders = sorted(user for user in all_authors if user not in {OWNER, "<unknown>"})
        unknown = "<unknown>" in all_authors
        lines = [
            f"Проверил данные GitHub API для {REPO}"
            + (" (из свежего локального кэша)." if cached else "."),
            f"Вопросы Issues: {snapshot['issues']['all']} всего, "
            f"{snapshot['issues']['open']} открыто, "
            f"{snapshot['issues']['closed']} закрыто.",
            _render_users("Авторы Issues", authors),
            f"Pull Requests: {snapshot['prs']['all']}. "
            + _render_users("Авторы PR", snapshot["prs"]["authors"]),
            f"Комментарии в Issues/PR: {snapshot['issue_comments']['all']}. "
            + _render_users("Авторы", snapshot["issue_comments"]["authors"]),
            f"Комментарии к строкам кода PR: {snapshot['inline_review_comments']['all']}. "
            + _render_users("Авторы", snapshot["inline_review_comments"]["authors"]),
        ]
        if outsiders:
            lines.append("Другие GitHub-аккаунты в проверенных категориях: "
                         + ", ".join(outsiders) + ".")
        elif unknown:
            lines.append("В некоторых записях автор не определён; поэтому "
                         "отсутствие чужих аккаунтов нельзя подтвердить.")
        else:
            lines.append("В этих категориях других аккаунтов, кроме " + OWNER + ", не найдено.")
        lines.append("Отдельные GitHub Discussions, комментарии к коммитам и "
                     "текстовые submissions PR reviews не проверялись. "
                     "GitHub-аккаунт не позволяет установить, писал текст человек или ассистент.")
        lines.append("Источник: GitHub REST API; снимок: " + snapshot["checked_at"] + ".")
        return {
            "answer": "\n".join(lines),
            "sources": sources,
            "verified": True,
            "receipt_id": receipt.receipt_id,
        }


__all__ = ["GitHubRepositoryEvidence", "GitHubEvidenceUnavailable",
           "is_leshy_repository_audit_request"]
