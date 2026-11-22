"""One-time RDP Web approval: only owned ticket/target, no raw credentials."""
from __future__ import annotations

from types import SimpleNamespace
import pytest

import leshy.superagent_rdp_launch_approval as approvals
from leshy.superagent_remote_permissions import VerifiedActor


class Policy:
    def _check_actor(self, actor):
        if actor.method != "web" or actor.owner_id != "alice":
            raise PermissionError("unverified")

    def require(self, actor, node, capability, *, observed_fingerprint):
        if capability != "rdp" or node != "192.0.2.10":
            raise PermissionError("unregistered")


class Service:
    def __init__(self):
        self.storage = object()
        self.state = "working"
        self.started = False

    def get(self, ticket_id, *, owner_id):
        if (ticket_id, owner_id) != ("SA-1", "alice"):
            raise PermissionError("not owned")
        return {
            "state": self.state, "id": ticket_id,
            "request_text": getattr(self, "request_text", "Проверь Windows RDP"),
            "created_at": "2026-11-22T10:00:00Z",
        }

    def start_authorized_native_rdp(self, *a, **kw):
        self.started = True


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    approvals._PENDING.clear()
    monkeypatch.setattr(approvals, "module_enabled", lambda *a: True)
    monkeypatch.setattr(approvals, "find_credential_file", lambda *a: object())
    monkeypatch.setattr(approvals, "_registered_pin", lambda *a: "a"*64)


def actor(cookie="web-cookie"):
    return VerifiedActor("alice", cookie, "web")


def params():
    return ("SA-1", "192.0.2.10", "rdp.example.org", "a"*64, "host.json")


def test_prepare_does_not_open_network_or_read_password():
    s = Service()
    prepared = approvals.prepare_rdp_approval(Policy(), actor(), s, *params())
    assert prepared["credentials_will_be_delegated"] is True
    assert len(prepared["approval_token"]) >= 30
    assert not s.started
    assert "password" not in str(prepared)


def test_one_time_token_allows_only_exact_owner_session_and_target():
    s = Service()
    policy = Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    callback = approvals.consume_rdp_approval(
        policy, actor(), s, token, *params()
    )
    callback("192.0.2.10", "rdp.example.org")
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, token, *params())
    with pytest.raises(PermissionError):
        callback("192.0.2.11", "rdp.example.org")


def test_token_is_burned_on_wrong_browser_session():
    s = Service()
    policy = Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor("other-session"), s, token, *params())
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, token, *params())


def test_mutated_tls_pin_or_credential_file_denied():
    s = Service()
    policy = Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    modified = list(params())
    modified[3] = "b"*64
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, token, *modified)


def test_revoking_ticket_during_handshake_prevents_delegation():
    s = Service()
    p = Policy()
    token = approvals.prepare_rdp_approval(p, actor(), s, *params())["approval_token"]
    check = approvals.consume_rdp_approval(p, actor(), s, token, *params())
    s.state = "stopped"
    with pytest.raises(PermissionError):
        check("192.0.2.10", "rdp.example.org")


def test_approval_expiry_rejected(monkeypatch):
    s = Service()
    p = Policy()
    token = approvals.prepare_rdp_approval(p, actor(), s, *params())["approval_token"]
    key = next(iter(approvals._PENDING))
    row = approvals._PENDING[key]
    approvals._PENDING[key] = (row[0], -1.0)
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(p, actor(), s, token, *params())


def test_unknown_or_forbidden_fields_are_not_accepted_by_route():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = (root / "src/leshy/web_server_impl14.py").read_text(encoding="utf-8")
    ui = (root / "web/superagent-dashboard.js").read_text(encoding="utf-8")
    assert '"/v1/superagent/rdp/start/prepare"' in code
    assert '"/v1/superagent/rdp/start/execute"' in code
    assert '"approval_token", "confirm"' in code
    assert 'payload.get("confirm") is not True' in code
    assert "window.confirm(text)" in ui
    assert "Пароль никогда не вводится в браузер" in ui
    assert "service.start_authorized_native_rdp(" in code



def test_existing_registered_session_blocks_prepare(monkeypatch):
    s = Service()
    s.rdp_live_status = lambda actor, ticket: {"registered": True}
    with pytest.raises(PermissionError, match="уже зарегистрирована"):
        approvals.prepare_rdp_approval(Policy(), actor(), s, *params())


def test_concurrent_consumption_admits_exactly_one_attempt():
    from concurrent.futures import ThreadPoolExecutor
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]

    def consume(_):
        try:
            approvals.consume_rdp_approval(policy, actor(), s, token, *params())
            return "accepted"
        except PermissionError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, (0, 1)))
    assert sorted(results) == ["accepted", "rejected"]


def test_lease_expiry_prevents_late_credential_delegation(monkeypatch):
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    callback = approvals.consume_rdp_approval(policy, actor(), s, token, *params())
    base = approvals.time.monotonic()
    monkeypatch.setattr(approvals.time, "monotonic", lambda: base + 120.0)
    with pytest.raises(PermissionError, match="истекло"):
        callback("192.0.2.10", "rdp.example.org")



def test_changed_task_burns_approval_even_if_returned_to_original():
    s = Service()
    token = approvals.prepare_rdp_approval(Policy(), actor(), s, *params())["approval_token"]
    s.request_text = "Another task was substituted"
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(Policy(), actor(), s, token, *params())
    s.request_text = "Проверь Windows RDP"
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(Policy(), actor(), s, token, *params())


def test_changed_task_after_consent_blocks_password_lease():
    s = Service()
    token = approvals.prepare_rdp_approval(Policy(), actor(), s, *params())["approval_token"]
    permit = approvals.consume_rdp_approval(Policy(), actor(), s, token, *params())
    s.request_text = "Изменено после выдачи"
    with pytest.raises(PermissionError):
        permit("192.0.2.10", "rdp.example.org")


def test_forbidden_at_creation_cannot_be_upgraded_by_new_login():
    s = Service()
    original_get = s.get
    def get(t, *, owner_id):
        item = original_get(t, owner_id=owner_id)
        item["mandate"] = {"remote_authorized_at_creation": False}
        return item
    s.get = get
    with pytest.raises(PermissionError):
        approvals.prepare_rdp_approval(Policy(), actor(), s, *params())
