from __future__ import annotations

from pathlib import Path
import tempfile

from leshy.app import LeshyApp
from leshy.config import Settings
from leshy.help_catalog import build_help_text, install_help_routing, is_help_request
from leshy.realtime_chat import RealtimeChatManager


def _settings(root: Path) -> Settings:
    brain = root / "LeshyBrain"
    return Settings(
        install_root=root,
        brain_dir=brain,
        data_dir=brain / "data",
        database_path=brain / "data" / "leshy.sqlite3",
        native_models_dir=brain / "models",
        workspace_root=brain / "workspace",
        llm_backend="echo",
        embeddings_enabled=False,
    )


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


def test_help_bypasses_llm_and_is_saved_to_history():
    install_help_routing(LeshyApp)
    with tempfile.TemporaryDirectory() as temp:
        app = LeshyApp(_settings(Path(temp)))
        result = app.chat("что ты умеешь?", owner_id="web:test", session_id="help-1")
        assert "ЧТО Я УМЕЮ СЕЙЧАС" in result["answer"]
        assert not result["answer"].startswith("Эхо:")
        history = app.history("web:test", "help-1")
        assert [item["role"] for item in history] == ["user", "assistant"]


def test_help_works_in_streaming_web_chat():
    install_help_routing(LeshyApp)
    with tempfile.TemporaryDirectory() as temp:
        app = LeshyApp(_settings(Path(temp)))
        manager = RealtimeChatManager(app)
        events = list(manager.stream("help", owner_id="web:test", session_id="help-stream", request_id="r-help"))
        assert events[0].type == "start"
        assert events[-1].type == "done"
        assert events[-1].data.get("help") is True
        assert "КАК СО МНОЙ РАБОТАТЬ" in events[-1].data["answer"]
