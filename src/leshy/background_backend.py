from __future__ import annotations

from typing import Any

from .adaptive_backend import AdaptiveLLM


class BackgroundAdaptiveLLM(AdaptiveLLM):
    """Маршрутизатор LLM для фоновых задач: NPU -> CPU -> GPU.

    Фоновый маршрут намеренно не подчиняется интерактивному переключателю
    аппаратного режима из Web UI. Пользователь может выбрать GPU для чата, но
    автономное обучение и другие фоновые LLM-задачи всё равно сначала используют
    NPU, затем CPU и только в крайнем случае GPU, чтобы не отнимать ускоритель у
    интерактивного ответа.

    Провайдеры можно разделять с интерактивным AdaptiveLLM: это не создаёт вторую
    копию модели/llama-server само по себе и позволяет использовать уже resident
    backend.
    """

    PRIORITY = ("npu", "cpu", "gpu", "other")

    def __init__(self, candidates: list[tuple[str, Any]], *, cooldown_seconds: float = 180.0):
        super().__init__(candidates, cooldown_seconds=cooldown_seconds, priority=self.PRIORITY)

    def _hardware_mode(self) -> str:
        # Интерактивная настройка hardware.json не должна менять контракт
        # фонового маршрута NPU -> CPU -> GPU.
        return "auto"

    @classmethod
    def from_interactive(cls, interactive: AdaptiveLLM) -> "BackgroundAdaptiveLLM":
        """Создать фоновый router поверх тех же provider-объектов.

        Состояние cooldown/quarantine у маршрутизаторов остаётся независимым, а
        тяжёлые runtime provider-объекты общие. Это важно: отдельный router не
        должен загружать ещё одну копию GGUF в VRAM/RAM.
        """
        candidates = [(state.name, state.provider) for state in interactive._states]
        return cls(candidates, cooldown_seconds=interactive.cooldown_seconds)
