"""Регрессии атомарности и изоляции одноразовых браузерных разрешений.

Эти тесты не запускаются автоматически при подготовке изменения.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from leshy.action_approvals import ApprovalBroker
from leshy.browser_agent import BrowserAction, BrowserRisk


def _financial_action() -> BrowserAction:
    return BrowserAction(
        "pay", "https://example.org/checkout", BrowserRisk.FINANCIAL,
        {"amount": 100, "currency": "RUB"},
    )


def test_other_owner_cannot_consume_or_deny_valid_approval():
    broker = ApprovalBroker()
    item = broker.request("owner-a", _financial_action())
    with pytest.raises(KeyError):
        broker.consume("owner-b", item.approval_id)
    with pytest.raises(KeyError):
        broker.deny("owner-b", item.approval_id)
    assert broker.consume("owner-a", item.approval_id) == _financial_action()


def test_denied_approval_cannot_be_replayed():
    broker = ApprovalBroker()
    item = broker.request("owner-a", _financial_action())
    broker.deny("owner-a", item.approval_id)
    with pytest.raises(KeyError):
        broker.consume("owner-a", item.approval_id)


def test_concurrent_consume_has_exactly_one_winner():
    broker = ApprovalBroker()
    item = broker.request("owner-a", _financial_action())
    barrier = Barrier(8)

    def attempt(_index: int) -> bool:
        barrier.wait(timeout=10)
        try:
            broker.consume("owner-a", item.approval_id)
        except KeyError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(True) == 1
    assert results.count(False) == 7
