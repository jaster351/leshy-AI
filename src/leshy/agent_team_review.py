"""Leshy's own bounded review envelope for multi-agent synthesis.

Role responses are UNTRUSTED analysis, not tool receipts. This module does
not call any model, tool, shell, network service or credential store.
It neither replaces Cognitive Core nor grants permissions or completion.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Sequence

MAX_TASK_CHARS = 12000
MAX_REPORT_CHARS = 4000
MAX_CONTEXT_CHARS = 6000
MAX_REPORTS = 16


@dataclass(frozen=True, slots=True)
class TeamReview:
    """Structural coverage only; never evidence that actions succeeded."""
    expected: tuple[str, ...]
    usable: tuple[str, ...]
    failed: tuple[str, ...]
    truncated: tuple[str, ...]
    context_truncated: bool
    duplicate_roles: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return bool(self.expected) and self.usable == self.expected and not self.duplicate_roles

    def public(self) -> dict[str, Any]:
        return {
            "scope": "model_reports_only",
            "state": "all_roles_reported" if self.complete else "partial_reports",
            "expected_roles": list(self.expected),
            "usable_roles": list(self.usable),
            "failed_roles": list(self.failed),
            "truncated_roles": list(self.truncated),
            "duplicate_roles": list(self.duplicate_roles),
            "context_truncated": self.context_truncated,
            "tool_execution_verified": False,
            "independent_factual_verification": False,
        }


def assemble_team_review(
    task: str, context: str, reports: Sequence[Any], expected_roles: Sequence[str],
) -> tuple[str, TeamReview]:
    """Bound evidence given to the synthesis LLM; ignore untrusted role labels.

    Owns no final truth decision. Do not silently turn missing roles, model
    claims or successful transport into proof of code execution.
    """
    if type(task) is not str or not task.strip() or len(task) > MAX_TASK_CHARS:
        raise ValueError("Multi-agent task must be non-empty and within 12000 characters")
    if type(context) is not str:
        raise ValueError("Multi-agent context must be plain text")
    roles = tuple(expected_roles)
    if (not roles or len(roles) > MAX_REPORTS
            or any(type(x) is not str or not x or len(x) > 80 for x in roles)
            or len(set(roles)) != len(roles)):
        raise ValueError("Invalid expected agent roles")

    # Only role names declared by the trusted coordinator can reach synthesis.
    accepted: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    failed: set[str] = set()
    trimmed: set[str] = set()
    for report in list(reports)[:MAX_REPORTS]:
        role = getattr(report, "role", None)
        if type(role) is not str or role not in roles:
            continue
        if role in accepted or role in failed:
            duplicates.add(role)
            continue
        answer = getattr(report, "answer", None)
        ok = getattr(report, "ok", None) is True
        if not ok or type(answer) is not str or not answer.strip():
            failed.add(role)
            continue
        text = answer.strip()
        if len(text) > MAX_REPORT_CHARS:
            text = text[:MAX_REPORT_CHARS] + "\n[Часть отчёта опущена из-за лимита контекста]"
            trimmed.add(role)
        node = getattr(report, "node_id", None)
        # Node ID is untrusted as display data; never forward arbitrary strings.
        safe_node = (
            node if type(node) is str and 0 < len(node) <= 80
            and all(ch.isascii() and (ch.isalnum() or ch in "._:-") for ch in node)
            else "unknown"
        )
        accepted[role] = {"role": role, "node": safe_node, "analysis": text}

    used = tuple(role for role in roles if role in accepted)
    missing = tuple(role for role in roles if role not in accepted)
    review = TeamReview(
        expected=roles, usable=used, failed=missing,
        truncated=tuple(role for role in roles if role in trimmed),
        context_truncated=len(context) > MAX_CONTEXT_CHARS,
        duplicate_roles=tuple(role for role in roles if role in duplicates),
    )
    payload = {
        "request": task,
        "relevant_context_untrusted": context[:MAX_CONTEXT_CHARS],
        "agent_reports_untrusted": [accepted[role] for role in used],
        "review_contract": review.public(),
    }
    # JSON quoting prevents a malicious report from modifying the surrounding
    # structure. The model is still instructed to treat its contents as data.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), review


__all__ = [
    "MAX_TASK_CHARS", "MAX_REPORT_CHARS", "MAX_CONTEXT_CHARS",
    "TeamReview", "assemble_team_review",
]
