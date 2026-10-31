from leshy.persona import LESHY_IDENTITY_PROMPT


def test_identity_declares_real_internet_capability():
    prompt = LESHY_IDENTITY_PROMPT.lower()
    assert "реальный доступ к интернету" in prompt
    assert "никогда не пиши, что у тебя «нет доступа к интернету»" in prompt


def test_identity_does_not_impose_blanket_tool_restrictions():
    prompt = LESHY_IDENTITY_PROMPT.lower()
    assert "не можешь использовать сторонние инструменты" in prompt
    assert "конкретный инструмент" in prompt
    assert "реально доступны текущей установке" in prompt


def test_identity_handles_physical_actions_by_actual_integrations():
    prompt = LESHY_IDENTITY_PROMPT.lower()
    assert "физические действия" in prompt
    assert "подключённые устройства" in prompt
    assert "не заявляй общего запрета" in prompt
