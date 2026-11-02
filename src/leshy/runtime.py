from __future__ import annotations

from .app import LeshyApp
from .config import Settings
from .identity_runtime import install_creator_identity_routing


class Runtime(LeshyApp):
    """Совместимый публичный Runtime поверх рабочего ядра 1.10.3.

    Старый миграционный runtime зависел от неперенесённых модулей. Этот фасад сохраняет
    базовую точку входа `Runtime(Settings)` и переводит её на единый рабочий контур,
    которым пользуются Web UI и Telegram.
    """

    def __init__(self, settings: Settings | None = None):
        install_creator_identity_routing(LeshyApp)
        super().__init__(settings or Settings.from_env())
        self.learning_control = self.learning
        # Совместимость с кодом, который ожидал `.agent.chat(...)`.
        self.agent = self

    def hardware_status(self) -> dict:
        return dict(self.status().get("hardware") or {})


__all__ = ["Runtime"]
