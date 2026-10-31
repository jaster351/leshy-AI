from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Iterable

from .llm import ChatMessage, LLMUnavailable


_SYSTEM_PROMPT = (
    "Ты Леший — локальный персональный AI-ассистент. Отвечай по-русски, если пользователь "
    "не просит другой язык. Будь точным, практичным и не выдумывай факты. Если данных не хватает, "
    "скажи об этом прямо. Не раскрывай скрытые внутренние рассуждения."
)


class NativeGGUFLLM:
    """Ленивая локальная GGUF-модель через llama-cpp-python.

    Модель загружается только при первом запросе. Это позволяет Web UI стартовать и показывать
    понятную диагностику даже до загрузки/установки файла модели.
    """

    def __init__(self, model_path: Path, *, n_ctx: int = 8192, n_gpu_layers: int | None = None):
        self.model_path = Path(model_path)
        self.n_ctx = int(n_ctx)
        self.n_gpu_layers = int(os.getenv("LESHY_GPU_LAYERS", "-1")) if n_gpu_layers is None else int(n_gpu_layers)
        self._model = None
        self._lock = threading.RLock()

    @property
    def model(self) -> str:
        return self.model_path.name

    def available(self) -> bool:
        return self.model_path.is_file()

    def _load(self):
        if self._model is not None:
            return self._model
        if not self.model_path.is_file():
            raise LLMUnavailable(
                "Локальная модель не найдена. Поместите Qwen3-8B-Q4_K_M.gguf в "
                f"{self.model_path.parent} или задайте LESHY_NATIVE_CHAT_FILE."
            )
        try:
            from llama_cpp import Llama
        except Exception as exc:  # pragma: no cover - зависит от окружения пользователя
            raise LLMUnavailable(
                "Не установлен или не запускается llama-cpp-python. Переустановите runtime Лешего."
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
        payload = [{"role": "system", "content": _SYSTEM_PROMPT}]
        payload.extend({"role": m.role, "content": m.content} for m in messages if m.role in {"user", "assistant", "system"})
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
        value = str(text or "").strip()
        if not value:
            raise LLMUnavailable("Локальная модель вернула пустой ответ")
        return value
