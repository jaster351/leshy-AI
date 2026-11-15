"""Регрессии неизменности подтверждённого браузерного действия.

Тесты не запускаются автоматически этим изменением.
"""
from leshy.action_approvals import ApprovalBroker
from leshy.browser_agent import BrowserAction, BrowserRisk


def test_request_captures_nested_arguments_snapshot():
    broker = ApprovalBroker()
    arguments = {"form": {"amount": 10, "items": ["one"]}}
    action = BrowserAction("submit", "https://example.org", BrowserRisk.MUTATE, arguments)
    pending = broker.request("owner-a", action)
    arguments["form"]["amount"] = 999
    arguments["form"]["items"].append("two")
    approved = broker.consume("owner-a", pending.approval_id)
    assert approved.arguments == {"form": {"amount": 10, "items": ["one"]}}


def test_consumed_action_does_not_share_original_arguments():
    broker = ApprovalBroker()
    action = BrowserAction("submit", "https://example.org", BrowserRisk.COMMIT, {"nested": {"value": "safe"}})
    pending = broker.request("owner-a", action)
    consumed = broker.consume("owner-a", pending.approval_id)
    consumed.arguments["nested"]["value"] = "changed"
    assert action.arguments["nested"]["value"] == "safe"


def test_approval_remains_single_use():
    broker = ApprovalBroker()
    pending = broker.request("owner-a", BrowserAction("submit", "https://example.org", BrowserRisk.MUTATE))
    broker.consume("owner-a", pending.approval_id)
    try:
        broker.consume("owner-a", pending.approval_id)
    except KeyError:
        pass
    else:
        raise AssertionError("Повторное использование approval должно быть запрещено")
