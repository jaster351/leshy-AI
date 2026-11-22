"""One-time RDP Web approval: only owned ticket/target, no raw credentials."""
from __future__ import annotations

from types import SimpleNamespace
import pytest

import leshy.superagent_rdp_launch_approval as approvals
from leshy.superagent_remote_permissions import VerifiedActor

_REAL_CREDENTIAL_IDENTITY = approvals._credential_identity


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
            "mandate": {"remote_authorized_at_creation": True},
        }

    def start_authorized_native_rdp(self, *a, **kw):
        self.started = True


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    approvals._PENDING.clear()
    approvals._TICKET_EPOCHS.clear()
    monkeypatch.setattr(approvals, "module_enabled", lambda *a: True)
    monkeypatch.setattr(approvals, "find_credential_file", lambda *a: object())
    monkeypatch.setattr(approvals, "_credential_identity",
                        lambda *a: ("dev", "ino", "1024", "mtime", "ctime"))
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



def test_rdp_approval_fails_on_generic_owner_ticket():
    s = Service()
    s.request_text = "Подготовь прогноз погоды"
    with pytest.raises(PermissionError, match="явно разрешить"):
        approvals.prepare_rdp_approval(Policy(), actor(), s, *params())


def test_rdp_approval_requires_creation_time_provider_identity():
    s = Service()
    original_get = s.get
    def get(t, *, owner_id):
        item = original_get(t, owner_id=owner_id)
        item["mandate"] = {}
        return item
    s.get = get
    with pytest.raises(PermissionError, match="явно разрешить"):
        approvals.prepare_rdp_approval(Policy(), actor(), s, *params())


def test_owner_tutorial_about_rdp_does_not_mint_credential_consent():
    s = Service()
    s.request_text = "Расскажи как настроить RDP на Windows"
    with pytest.raises(PermissionError):
        approvals.prepare_rdp_approval(Policy(), actor(), s, *params())


def test_new_login_cannot_upgrade_explicitly_denied_rdp_ticket():
    s = Service()
    original_get = s.get
    def get(t, *, owner_id):
        item = original_get(t, owner_id=owner_id)
        item["mandate"] = {"remote_authorized_at_creation": False}
        return item
    s.get = get
    with pytest.raises(PermissionError):
        approvals.prepare_rdp_approval(Policy(), actor(), s, *params())


def test_approval_respects_source_creation_timestamp_change():
    s = Service()
    original_get = s.get
    s.created_at = "2026-11-22T10:00:00Z"
    def get(t, *, owner_id):
        item = original_get(t, owner_id=owner_id)
        item["created_at"] = s.created_at
        return item
    s.get = get
    token = approvals.prepare_rdp_approval(Policy(), actor(), s, *params())["approval_token"]
    s.created_at = "2026-11-22T11:00:00Z"
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(Policy(), actor(), s, token, *params())



def test_file_replacement_after_prepare_burns_token(monkeypatch):
    s, policy = Service(), Policy()
    original = ("dev", "inode-A", "1024", "mtime", "ctime")
    stamp = [original]
    monkeypatch.setattr(approvals, "_credential_identity", lambda *a: stamp[0])
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    stamp[0] = ("dev", "inode-B", "1024", "mtime", "ctime")
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, token, *params())
    assert not approvals._PENDING


def test_file_modified_after_consumption_revokes_delegation(monkeypatch):
    s, policy = Service(), Policy()
    stamp = [("dev", "inode-A", "1024", "mtime-1", "ctime")]
    monkeypatch.setattr(approvals, "_credential_identity", lambda *a: stamp[0])
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    consent = approvals.consume_rdp_approval(policy, actor(), s, token, *params())
    stamp[0] = ("dev", "inode-A", "1024", "mtime-2", "ctime")
    with pytest.raises(PermissionError):
        consent("192.0.2.10", "rdp.example.org")


def test_preparation_rejects_symlink_and_oversized_file(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    target.write_bytes(b"{}")
    link = tmp_path / "shortcut.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not permitted on this Windows host")
    monkeypatch.setattr(approvals, "find_credential_file",
                        lambda storage, owner, basename: link)
    with pytest.raises(PermissionError):
        _REAL_CREDENTIAL_IDENTITY(object(), "alice", "shortcut.json")
    target.write_bytes(b"x" * 8193)
    monkeypatch.setattr(approvals, "find_credential_file",
                        lambda storage, owner, basename: target)
    with pytest.raises(PermissionError):
        _REAL_CREDENTIAL_IDENTITY(object(), "alice", "data.json")


def test_preparation_never_reads_secret_contents(tmp_path, monkeypatch):
    source = tmp_path / "rdp.json"
    source.write_bytes(b'{"password":"not_for_web"}')
    monkeypatch.setattr(approvals, "find_credential_file",
                        lambda storage, owner, basename: source)
    fingerprint = _REAL_CREDENTIAL_IDENTITY(object(), "alice", "rdp.json")
    assert len(fingerprint) == 5
    assert "not_for_web" not in str(fingerprint)



def test_owner_can_revoke_prepared_rdp_token_before_tcp():
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    assert approvals.cancel_rdp_approval(policy, actor(), token)
    assert not approvals.cancel_rdp_approval(policy, actor(), token)
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, token, *params())


def test_foreign_browser_cannot_revoke_owner_token():
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    assert not approvals.cancel_rdp_approval(policy, actor("other-session"), token)
    approvals.consume_rdp_approval(policy, actor(), s, token, *params())


def test_cancelled_consent_never_issues_a_delegation_lease():
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    assert approvals.cancel_rdp_approval(policy, actor(), token)
    assert len(approvals._PENDING) == 0
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, token, *params())



def test_stop_invalidates_unconsumed_token_before_native_registration():
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    assert approvals.invalidate_rdp_ticket_approvals("alice", "SA-1") == 1
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, token, *params())


def test_stop_invalidates_already_consumed_credential_delegation_lease():
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    consent = approvals.consume_rdp_approval(policy, actor(), s, token, *params())
    approvals.invalidate_rdp_ticket_approvals("alice", "SA-1")
    with pytest.raises(PermissionError, match="revoked"):
        consent("192.0.2.10", "rdp.example.org")


def test_new_owner_approval_after_stop_can_be_reviewed_afresh():
    s, policy = Service(), Policy()
    old = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    approvals.invalidate_rdp_ticket_approvals("alice", "SA-1")
    new = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    assert new != old
    consent = approvals.consume_rdp_approval(policy, actor(), s, new, *params())
    consent("192.0.2.10", "rdp.example.org")
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, old, *params())


def test_new_prepare_burns_previous_unconsumed_token_for_same_browser_ticket():
    s, policy = Service(), Policy()
    old = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    new = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, old, *params())
    approvals.consume_rdp_approval(policy, actor(), s, new, *params())


def test_global_superagent_shutdown_revokes_consumed_lease():
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    consent = approvals.consume_rdp_approval(policy, actor(), s, token, *params())
    approvals.invalidate_all_rdp_approvals()
    with pytest.raises(PermissionError, match="revoked"):
        consent("192.0.2.10", "rdp.example.org")


def test_pending_approval_count_is_bounded_per_owner(monkeypatch):
    s, policy = Service(), Policy()
    monkeypatch.setattr(approvals, "_MAX_PENDING_PER_OWNER", 2)
    # One verified owner, three different live browser sessions. The token
    # rotation rule is per-session, so separate sessions consume capacity.
    for session in ("web-1", "web-2"):
        approvals.prepare_rdp_approval(policy, actor(session), s, *params())
    with pytest.raises(PermissionError, match="лимит"):
        approvals.prepare_rdp_approval(policy, actor("web-3"), s, *params())


def test_owner_stop_epoch_is_scoped_to_ticket(monkeypatch):
    s, policy = Service(), Policy()
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    approvals.invalidate_rdp_ticket_approvals("alice", "SA-OTHER")
    approvals.consume_rdp_approval(policy, actor(), s, token, *params())


def test_ticket_stop_invalidates_most_recent_review_nonce():
    s, policy = Service(), Policy()
    old = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    latest = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    assert latest != old
    assert approvals.invalidate_rdp_ticket_approvals("alice", "SA-1") == 1
    with pytest.raises(PermissionError):
        approvals.consume_rdp_approval(policy, actor(), s, latest, *params())


def test_expired_stopped_ticket_epoch_allows_only_new_short_lived_consent(monkeypatch):
    # Simulate expiry only after all already issued tokens and leases would
    # have independently expired. A brand-new preparation is allowed.
    s, policy = Service(), Policy()
    approvals.invalidate_rdp_ticket_approvals("alice", "SA-1")
    key = ("alice", "SA-1")
    prior = approvals._TICKET_EPOCHS[key]
    approvals._TICKET_EPOCHS[key] = (prior[0], -1.0)
    new = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    approvals.consume_rdp_approval(policy, actor(), s, new, *params())



def test_explicit_owner_ip_prevents_switch_to_another_registered_node():
    s, policy = Service(), Policy()
    s.request_text = "Подключись по RDP к 192.0.2.11 и проверь экран"
    with pytest.raises(PermissionError, match="не совпадает"):
        approvals.prepare_rdp_approval(policy, actor(), s, *params())


def test_exact_explicit_owner_ip_is_allowed():
    s, policy = Service(), Policy()
    s.request_text = "Подключись по RDP к 192.0.2.10 и проверь экран"
    token = approvals.prepare_rdp_approval(policy, actor(), s, *params())["approval_token"]
    approvals.consume_rdp_approval(policy, actor(), s, token, *params())


def test_ambiguous_multiple_explicit_ip_targets_are_denied():
    s, policy = Service(), Policy()
    s.request_text = "Подключись по RDP к 192.0.2.10 или 192.0.2.11"
    with pytest.raises(PermissionError, match="не совпадает"):
        approvals.prepare_rdp_approval(policy, actor(), s, *params())


def test_invalid_literal_ip_cannot_be_silently_ignored():
    s, policy = Service(), Policy()
    s.request_text = "Открой RDP на 999.999.999.999"
    with pytest.raises(PermissionError, match="неверный IP"):
        approvals.prepare_rdp_approval(policy, actor(), s, *params())


def test_conflicting_original_mandate_scope_fails_closed():
    s, policy = Service(), Policy()
    original = s.get
    def get(t, *, owner_id):
        data = original(t, owner_id=owner_id)
        data["mandate"] = {
            "remote_authorized_at_creation": True,
            "scope": "Подключись к другому компьютеру",
        }
        return data
    s.get = get
    with pytest.raises(PermissionError, match="явно разрешить"):
        approvals.prepare_rdp_approval(policy, actor(), s, *params())


def test_other_owner_in_original_mandate_denied_even_if_ticket_lookup_passes():
    s, policy = Service(), Policy()
    original = s.get
    def get(t, *, owner_id):
        data = original(t, owner_id=owner_id)
        data["mandate"] = {
            "remote_authorized_at_creation": True, "owner_id": "other-owner",
        }
        return data
    s.get = get
    with pytest.raises(PermissionError):
        approvals.prepare_rdp_approval(policy, actor(), s, *params())


def test_changed_original_creation_timestamp_in_mandate_denied():
    s, policy = Service(), Policy()
    original = s.get
    def get(t, *, owner_id):
        data = original(t, owner_id=owner_id)
        data["mandate"] = {
            "remote_authorized_at_creation": True,
            "created_at": "2020-01-01T00:00:00Z",
        }
        return data
    s.get = get
    with pytest.raises(PermissionError):
        approvals.prepare_rdp_approval(policy, actor(), s, *params())


def test_explicit_ipv6_node_mismatch_is_refused_before_file_read():
    s, policy = Service(), Policy()
    s.request_text = "Подключись по RDP к [2001:db8::7]"
    with pytest.raises(PermissionError, match="не совпадает"):
        approvals.prepare_rdp_approval(policy, actor(), s, *params())
