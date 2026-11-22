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



def _native_ticket_storage():
    """Tiny real SQLite schema for the durable ticket state transition."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE superagent_tickets (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT '',
        request_text TEXT NOT NULL,
        mandate_json TEXT NOT NULL,
        latest_report TEXT NOT NULL DEFAULT '',
        attention TEXT NOT NULL DEFAULT '',
        sources_json TEXT NOT NULL DEFAULT '[]',
        next_run_epoch REAL NOT NULL DEFAULT 0,
        delivered INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE superagent_assignments (
        id TEXT PRIMARY KEY, ticket_id TEXT, attempt INTEGER,
        state TEXT, started_at TEXT, ended_at TEXT, outcome TEXT,
        error TEXT
    );
    CREATE TABLE superagent_steps (
        ticket_id TEXT, status TEXT, finished_at TEXT
    );
    """)
    class Storage:
        def connection(self):
            return conn
    return conn, Storage()


def test_real_sqlite_transition_keeps_rdp_ticket_working_for_web_consent(monkeypatch):
    import threading
    from types import SimpleNamespace
    from leshy.superagent import SuperAgentService
    import leshy.module_settings as settings
    monkeypatch.setattr(settings, "module_enabled", lambda *a: True)
    conn, storage = _native_ticket_storage()
    request = "Подключись к Windows через RDP и проверь экран"
    mandate = '{"remote_authorized_at_creation":true}'
    conn.execute(
        "INSERT INTO superagent_tickets(id,state,request_text,mandate_json) "
        "VALUES(?,?,?,?)", ("SA-DB-1", "queued", request, mandate)
    )
    task = {
        "id": "SA-DB-1", "attempt": 0,
        "request_text": request, "mandate_json": mandate,
    }
    class Service:
        _lease = SimpleNamespace(held=True)
        _stop = threading.Event()

        def _execute(self, ticket):
            return SuperAgentService._execute(self, ticket)

        def _deliver(self, ticket_id):
            pass
    Service.storage = storage
    # No network, model or credential access: same production state update.
    SuperAgentService._run_ticket(Service(), task)
    row = conn.execute(
        "SELECT state,attempt,latest_report FROM superagent_tickets WHERE id='SA-DB-1'"
    ).fetchone()
    assert row["state"] == "working" and row["attempt"] == 1
    assert "Соединение ещё не установлено" in row["latest_report"]
    assignment = conn.execute(
        "SELECT state,outcome FROM superagent_assignments WHERE ticket_id='SA-DB-1'"
    ).fetchone()
    assert assignment["state"] == "ended" and assignment["outcome"] == "working"
    conn.close()


def test_real_sqlite_rdp_recovery_fails_closed_instead_of_requeueing():
    from types import SimpleNamespace
    from leshy.superagent import SuperAgentService
    conn, storage = _native_ticket_storage()
    request = "Подключись к Windows через RDP и проверь экран"
    conn.execute(
        "INSERT INTO superagent_tickets(id,state,attempt,request_text,mandate_json) "
        "VALUES(?,?,?,?,?)",
        ("SA-DB-2", "working", 1, request,
         '{"remote_authorized_at_creation":true}'),
    )
    conn.execute(
        "INSERT INTO superagent_assignments(id,ticket_id,attempt,state,started_at) "
        "VALUES(?,?,?,?,?)",
        ("SA-DB-2-A1", "SA-DB-2", 1, "running", "start"),
    )
    class Service:
        max_attempts = 3
    Service.storage = storage
    SuperAgentService._recover_interrupted(Service())
    row = conn.execute(
        "SELECT state,next_run_epoch,latest_report FROM superagent_tickets "
        "WHERE id='SA-DB-2'"
    ).fetchone()
    assert row["state"] == "blocked"
    assert row["next_run_epoch"] == 0
    assert "Состояние удалённого рабочего стола неизвестно" in row["latest_report"]
    row = conn.execute(
        "SELECT state,outcome FROM superagent_assignments "
        "WHERE ticket_id='SA-DB-2'"
    ).fetchone()
    assert row["state"] == "ended" and row["outcome"] == "interrupted"
    conn.close()
