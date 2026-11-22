"""Original mandate must explicitly request RDP, never tutorial/negation."""
from __future__ import annotations
import pytest
from leshy.superagent import _rdp_explicit_task


@pytest.mark.parametrize("message", [
    "Подключись к Windows 10 через RDP и проверь рабочий стол",
    "Открой удалённый рабочий стол на моём ПК",
    "Connect to remote desktop for read-only inspection",
    "Проверь RDP подключение к зарегистрированному компьютеру",
    "Подключайся по RDP к моему зарегистрированному компьютеру",
    "Inspect remote desktop and report any error",
])
def test_explicit_rdp_intent(message):
    assert _rdp_explicit_task(message)


@pytest.mark.parametrize("message", [
    "Привет, какая сегодня дата?",
    "Расскажи как настроить RDP",
    "Не подключайся через RDP",
    "Как подключиться к удалённому рабочему столу?",
    "Объясни RDP",
    "Не открывай удаленный рабочий стол",
    "Нужен SSH, не RDP",
    "Не подключайся к RDP на сервере",
    "Что такое RDP?",
    "Покажи инструкцию по RDP подключению",
    "RDP на Windows, помоги разобраться",
    "Как использовать RDP?",
    "Почему RDP не работает?",
])
def test_non_authorizing_requests(message):
    assert not _rdp_explicit_task(message)


def test_approval_file_has_no_literal_nul_bytes():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/leshy/superagent_rdp_launch_approval.py").read_bytes()
    assert b"\0" not in source



def test_rdp_ticket_is_held_working_for_explicit_owner_web_approval(monkeypatch):
    from types import SimpleNamespace
    from leshy.superagent import SuperAgentService
    import leshy.module_settings as settings

    monkeypatch.setattr(settings, "module_enabled", lambda *args: True)
    service = SimpleNamespace(storage=object())
    ticket = {
        "id": "SA-001",
        "request_text": "Подключись к Windows через RDP и проверь рабочий стол",
        "mandate_json": '{"remote_authorized_at_creation":true}',
    }
    result = SuperAgentService._execute(service, ticket)
    assert result[0] == "working"
    assert "Соединение ещё не установлено" in result[1]
    assert result[2:] == ("", [])


def test_rdp_ticket_without_identity_at_creation_is_blocked(monkeypatch):
    from types import SimpleNamespace
    from leshy.superagent import SuperAgentService
    import leshy.module_settings as settings

    monkeypatch.setattr(settings, "module_enabled", lambda *args: True)
    service = SimpleNamespace(storage=object())
    ticket = {
        "id": "SA-002",
        "request_text": "Открой удалённый рабочий стол",
        "mandate_json": '{"remote_authorized_at_creation":false}',
    }
    result = SuperAgentService._execute(service, ticket)
    assert result[0] == "blocked"
    assert "не разрешён" in result[1]


def test_rdp_ticket_cannot_wait_when_remote_admin_disabled(monkeypatch):
    from types import SimpleNamespace
    from leshy.superagent import SuperAgentService
    import leshy.module_settings as settings

    monkeypatch.setattr(
        settings, "module_enabled",
        lambda storage, name: name != "remote-admin"
    )
    ticket = {
        "id": "SA-002",
        "request_text": "Открой удалённый рабочий стол",
        "mandate": {"remote_authorized_at_creation": True},
    }
    result = SuperAgentService._execute(
        SimpleNamespace(storage=object()), ticket
    )
    assert result[0] == "blocked"


def test_rdp_recovery_is_never_requeued_after_restart():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/leshy/superagent.py").read_text(encoding="utf-8")
    part = source.split("def _recover_interrupted", 1)[1].split("def start(", 1)[0]
    assert 'if _rdp_explicit_task(str(row["request_text"] or ""))' in part
    assert "state='blocked'" in part


def test_rdp_retry_is_explicitly_denied_even_on_failed_ticket():
    from leshy.superagent import SuperAgentService
    class FakeService:
        def get(self, ticket_id, *, owner_id):
            return {"state": "failed", "request_text": "Подключись к Windows через RDP"}
    with pytest.raises(PermissionError, match="нельзя автоматически повторить"):
        SuperAgentService.retry(FakeService(), "SA-001", "owner")
