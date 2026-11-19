from __future__ import annotations

from .app import LeshyApp
from .chat_idempotency import install_global_chat_idempotency
from .semantic_research_router import install_semantic_research_router
from .config import Settings
from .freshness_cache_runtime import install_freshness_cache_runtime
from .freshness_memory_runtime import install_freshness_memory_runtime
from .runtime_latency_fix import install_runtime_latency_fixes


class Runtime(LeshyApp):
    """Совместимый публичный Runtime поверх рабочего ядра 1.10.3.

    Старый миграционный runtime зависел от неперенесённых модулей. Этот фасад сохраняет
    базовую точку входа `Runtime(Settings)` и переводит её на единый рабочий контур,
    которым пользуются Web UI и Telegram.
    """

    def __init__(self, settings: Settings | None = None):
        # Freshness/cache ставятся до latency-layer, чтобы финальная компактизация
        # учитывала stale fallback и свежие локальные dynamic-знания.
        install_freshness_memory_runtime()
        install_freshness_cache_runtime()
        install_runtime_latency_fixes()
        # Проверки документов и фактов дают модели данные через conversation_tool_evidence.
        # Не устанавливаем маршруты, которые возвращают готовые ответы без LLM.
        # Idempotency не допускает повторного выполнения одного request_id.
        install_global_chat_idempotency()
        install_semantic_research_router()
        super().__init__(settings or Settings.from_env())
        install_freshness_memory_runtime(self)
        self.learning_control = self.learning
        # Совместимость с кодом, который ожидал `.agent.chat(...)`.
        self.agent = self

    def hardware_status(self) -> dict:
        return dict(self.status().get("hardware") or {})


__all__ = ["Runtime"]
