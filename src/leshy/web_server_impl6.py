from __future__ import annotations

from . import web_server_impl5 as previous
from .app import LeshyApp
from .learning_command_runtime import install_explicit_learning_routing
from .voice_tts_ru import RussianLocalVoiceTTS


def _install_russian_voice_tts() -> None:
    """Подменить TTS-класс во всей совместимой цепочке Web-server.

    Старые impl-модули оставляем нетронутыми: это сохраняет проверенный startup,
    streaming и inference scheduler. Меняется только создаваемый TTS manager.
    """
    module = previous
    seen: set[int] = set()
    for _ in range(10):
        if module is None or id(module) in seen:
            break
        seen.add(id(module))
        if hasattr(module, "LocalVoiceTTS"):
            setattr(module, "LocalVoiceTTS", RussianLocalVoiceTTS)
        module = getattr(module, "previous", None)


def make_handler(app, telegram, settings, model_catalog):
    install_explicit_learning_routing(LeshyApp)
    _install_russian_voice_tts()
    return previous.make_handler(app, telegram, settings, model_catalog)


def serve(host: str | None = None, port: int | None = None) -> None:
    # Маршрутизация ставится один раз до создания Web/Telegram
    # RealtimeChatManager. Саму цепочку фабрик HTTP-handler не подменяем.
    install_explicit_learning_routing(LeshyApp)
    _install_russian_voice_tts()
    previous.serve(host=host, port=port)


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
