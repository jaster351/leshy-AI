from __future__ import annotations

from .app import LeshyApp
from .chat_idempotency import install_global_chat_idempotency
from .config import Settings
from .freshness_memory_runtime import install_freshness_memory_runtime
from .identity_runtime import install_creator_identity_routing
from .runtime_latency_fix import install_runtime_latency_fixes


class Runtime(LeshyApp):
    """Совместимый публичный Runtime поверх рабочего ядра 1.10.3.

    Старый миграционный runtime зависел от неперенесённых модулей. Этот фасад сохраняет
    базовую точку входа `Runtime(Settings)` и переводит её на единый рабочий контур,
    которым пользуются Web UI и Telegram.
    """

    def __init__(self, settings: Settings | None = None):
        # Freshness ставится до latency-layer, чтобы финальная компактизация
        # контекста учитывала stale fallback и не выбрасывала его за token budget.
        install_freshness_memory_runtime()
        install_runtime_latency_fixes()
        install_creator_identity_routing(LeshyApp)
        # Idempotency ставится последним поверх специальных chat routing, чтобы
        # повтор одного request_id не создавал второй ответ ни в одном интерфейсе.
        install_global_chat_idempotency()
        super().__init__(settings or Settings.from_env())
        install_freshness_memory_runtime(self)
        self.learning_control = self.learning
        # Совместимость с кодом, который ожидал `.agent.chat(...)`.
        self.agent = self

    def hardware_status(self) -> dict:
        return dict(self.status().get("hardware") or {})


__all__ = ["Runtime"]
