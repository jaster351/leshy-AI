from __future__ import annotations

from . import web_server_impl5 as previous
from .app import LeshyApp
from .learning_command_runtime import install_explicit_learning_routing


def make_handler(app, telegram, settings, model_catalog):
    install_explicit_learning_routing(LeshyApp)
    return previous.make_handler(app, telegram, settings, model_catalog)


def serve(host: str | None = None, port: int | None = None) -> None:
    # Маршрутизация ставится один раз до создания Web/Telegram
    # RealtimeChatManager. Саму цепочку фабрик HTTP-handler не подменяем.
    install_explicit_learning_routing(LeshyApp)
    previous.serve(host=host, port=port)


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
