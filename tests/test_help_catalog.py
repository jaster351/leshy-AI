from __future__ import annotations

from leshy.help_catalog import build_help_text, is_help_request


def test_help_aliases_are_recognized():
    for text in (
        "help",
        "/help",
        "помощь",
        "СПРАВКА",
        "Что ты умеешь?",
        "как с тобой работать",
        "Как тобой пользоваться?",
    ):
        assert is_help_request(text), text


def test_normal_chat_is_not_help():
    assert not is_help_request("Расскажи про погоду")
    assert not is_help_request("Помощь с настройкой Docker")


def test_help_describes_current_capabilities_and_installation():
    text = build_help_text()
    assert "КАК СО МНОЙ РАБОТАТЬ" in text
    assert "ЧТО Я УМЕЮ СЕЙЧАС" in text
    assert "Browser Agent" in text
    assert "Telegram" in text
    assert "Долговременная память" in text
    assert "Linux, macOS и Windows" in text
    assert "Аквариум Лешего" in text
