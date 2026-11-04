from __future__ import annotations

import pytest

from leshy.browser_agent import BrowserAction, BrowserRisk
from leshy.browser_use_adapter import BrowserUseAdapter


@pytest.mark.parametrize(
    ("name", "risk"),
    [
        ("agent_financial", BrowserRisk.READ),
        ("agent_commit", BrowserRisk.READ),
        ("agent_mutate", BrowserRisk.READ),
        ("agent_read", BrowserRisk.FINANCIAL),
        ("research", BrowserRisk.MUTATE),
        ("agent_financial", BrowserRisk.COMMIT),
    ],
)
def test_browser_use_rejects_action_risk_mismatch_before_backend(name, risk):
    # Backend не требуется: проверка должна завершиться до available()/launch.
    adapter = BrowserUseAdapter.__new__(BrowserUseAdapter)
    result = adapter.execute(BrowserAction(name, "https://example.com", risk))
    assert result.status == "blocked"
    assert "уровню риска" in result.message


def test_browser_use_still_requires_approval_for_mutation():
    adapter = BrowserUseAdapter.__new__(BrowserUseAdapter)
    result = adapter.execute(
        BrowserAction("agent_mutate", "https://example.com", BrowserRisk.MUTATE)
    )
    assert result.status == "blocked"
    assert "Human Approval Gate" in result.message
