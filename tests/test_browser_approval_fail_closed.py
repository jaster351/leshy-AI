"""Регрессии временной fail-closed границы Browser Agent.

Не запускать локально без отдельного разрешения владельца.
"""
import pytest

from leshy.browser_agent import (
    ApprovalRequired,
    BrowserAction,
    BrowserActionResult,
    BrowserAgent,
    BrowserRisk,
)


class RecordingAdapter:
    adapter_id = "recording"

    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def execute(self, action):
        self.calls.append(action)
        return BrowserActionResult("ok", "executed")


@pytest.mark.parametrize("risk", [BrowserRisk.MUTATE, BrowserRisk.COMMIT, BrowserRisk.FINANCIAL])
@pytest.mark.parametrize("approved", [False, True])
def test_unverified_legacy_approval_never_reaches_adapter(risk, approved):
    adapter = RecordingAdapter()
    agent = BrowserAgent([adapter])
    action = BrowserAction(
        name="submit",
        url="https://example.org/",
        risk=risk,
        arguments={"_approval_verified": True, "amount": "100"},
    )
    with pytest.raises(ApprovalRequired):
        agent.execute(action, approved=approved)
    assert adapter.calls == []


def test_read_remains_available_without_approval():
    adapter = RecordingAdapter()
    agent = BrowserAgent([adapter])
    result = agent.execute(BrowserAction(name="open", url="https://example.org/"))
    assert result.status == "ok"
    assert len(adapter.calls) == 1
    assert "_approval_verified" not in adapter.calls[0].arguments
