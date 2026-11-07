from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Callable, Iterable

from .llm import ChatMessage, LLMUnavailable, finalize_model_output
from .context_budget import _normalize_system_messages


_SYSTEM_PROMPT = (
    "Ты Леший — локальный персональный AI-ассистент. Отвечай по-русски, если пользователь "
    "не просит другой язык. Будь точным, практичным и не выдумывай факты. Если данных не хватает, "
    "скажи об этом прямо. Никогда не показывай пользователю внутренние рассуждения, анализ задачи, "
    "скрытый черновик, chain-of-thought или инструкции самому себе. Возвращай только готовый ответ пользователю."
)


class NativeGGUFLLM:
    """Ленивая локальная GGUF-модель через llama-cpp-python.

    Модель загружается только при первом запросе. Это позволяет Web UI стартовать и показывать
    понятную диагностику даже до загрузки/установки файла модели.
    """

    def __init__(self, model_path: Path, *, n_ctx: int = 8192, n_gpu_layers: int | None = None,
                 ensure_model: Callable[[], Path] | None = None):
        self.model_path = Path(model_path)
        self.n_ctx = int(n_ctx)
        self.n_gpu_layers = int(os.getenv("LESHY_GPU_LAYERS", "-1")) if n_gpu_layers is None else int(n_gpu_layers)
        self._model = None
        self.ensure_model = ensure_model
        self._lock = threading.RLock()

    @property
    def model(self) -> str:
        return self.model_path.name

    def available(self) -> bool:
        return self.model_path.is_file()

    def _load(self):
        if self._model is not None:
            return self._model
        if not self.model_path.is_file() and self.ensure_model is not None:
            self.model_path = Path(self.ensure_model())
        if not self.model_path.is_file():
            raise LLMUnavailable(
                "GGUF-модель отсутствует: "
                f"{self.model_path}. Укажите LESHY_NATIVE_CHAT_FILE или установите модель."
            )
        try:
            from llama_cpp import Llama
        except Exception as exc:  # pragma: no cover - зависит от окружения пользователя
            raise LLMUnavailable(
                "llama-cpp-python не удалось импортировать: "
                f"{type(exc).__name__}: {str(exc)[:240]}. "
                "Проверьте локальную сборку runtime для этой ОС."
            ) from exc
        try:
            self._model = Llama(
                model_path=str(self.model_path),
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                verbose=False,
            )
        except Exception as exc:  # pragma: no cover - зависит от железа/драйвера
            raise LLMUnavailable(f"Не удалось загрузить локальную модель: {exc}") from exc
        return self._model

    def chat(self, messages: Iterable[ChatMessage]) -> str:
        prepared = _normalize_system_messages([ChatMessage("system", _SYSTEM_PROMPT), *[m for m in messages if m.role in {"user", "assistant", "system"}]])
        payload = [{"role": m.role, "content": m.content} for m in prepared]
        with self._lock:
            model = self._load()
            try:
                result = model.create_chat_completion(
                    messages=payload,
                    temperature=float(os.getenv("LESHY_TEMPERATURE", "0.25")),
                    max_tokens=int(os.getenv("LESHY_MAX_TOKENS", "1024")),
                )
                text = result["choices"][0]["message"]["content"]
            except Exception as exc:  # pragma: no cover - зависит от backend
                raise LLMUnavailable(f"Ошибка локальной модели: {exc}") from exc
        value = finalize_model_output(str(text or ""))
        if not value:
            raise LLMUnavailable("Локальная модель вернула пустой пользовательский ответ")
        return value