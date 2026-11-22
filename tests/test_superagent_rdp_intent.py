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
