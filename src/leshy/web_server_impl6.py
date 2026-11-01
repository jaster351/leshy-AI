from __future__ import annotations

from . import web_server_impl5 as previous
from .app import LeshyApp
from .learning_command_runtime import install_explicit_learning_routing


def make_handler(app, telegram, settings, model_catalog):
    install_explicit_learning_routing(LeshyApp)
    return previous.make_handler(app, telegram, settings, model_catalog)


def serve(host: str | None = None, port: int | None = None) -> None:
    # Маршрутизация ставится до создания Web/Telegram RealtimeChatManager,
    # поэтому все каналы получают одинаковое поведение «изучи/научись».
    install_explicit_learning_routing(LeshyApp)
    original = previous.make_handler
    previous.make_handler = make_handler
    try:
        previous.serve(host=host, port=port)
    finally:
        previous.make_handler = original


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
