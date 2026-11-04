from __future__ import annotations

import pytest

from leshy.action_approvals import ApprovalBroker
from leshy.browser_agent import BrowserAction, BrowserRisk


def _action() -> BrowserAction:
    return BrowserAction("submit", "https://example.com/form", BrowserRisk.MUTATE)


def test_long_owner_id_is_rejected_instead_of_truncated():
    broker = ApprovalBroker()
    with pytest.raises(ValueError, match="200"):
        broker.request("a" * 200 + "one", _action())
    assert broker.list("a" * 200) == []


def test_two_long_owner_ids_cannot_collide_on_first_200_characters():
    broker = ApprovalBroker()
    valid_owner = "a" * 200
    approval = broker.request(valid_owner, _action())
    with pytest.raises(ValueError, match="200"):
        broker.consume(valid_owner + "other", approval.approval_id)
    assert broker.consume(valid_owner, approval.approval_id) == approval.action


@pytest.mark.parametrize("operation", ["list", "deny", "consume"])
def test_overlong_owner_rejected_for_all_approval_accessors(operation):
    broker = ApprovalBroker()
    owner = "b" * 201
    with pytest.raises(ValueError, match="200"):
        if operation == "list":
            broker.list(owner)
        else:
            getattr(broker, operation)(owner, "unknown")


def test_whitespace_is_normalized_without_truncating_valid_owner():
    broker = ApprovalBroker()
    owner = "x" * 200
    approval = broker.request("  " + owner + "  ", _action())
    assert broker.list(owner)[0]["approval_id"] == approval.approval_id
