from __future__ import annotations

import os
import json
import time
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
                 ensure_model: Callable[[], Path] | None = None, brain_dir: Path | None = None):
        self.model_path = Path(model_path)
        self.n_ctx = int(n_ctx)
        self.n_gpu_layers = int(os.getenv("LESHY_GPU_LAYERS", "-1")) if n_gpu_layers is None else int(n_gpu_layers)
        self._model = None
        self.ensure_model = ensure_model
        self._lock = threading.RLock()
        self._brain_dir = Path(brain_dir) if brain_dir is not None else None
        self._idle_sleep_enabled = True
        self._idle_sleep_seconds = 60
        self._idle_last_used = time.monotonic()
        self._idle_sleep_unloads = 0
        self._idle_sleep_stop = threading.Event()
        self._load_idle_sleep_config()
        self._idle_sleep_thread = threading.Thread(
            target=self._idle_sleep_watch,
            name="leshy-native-idle-model",
            daemon=True,
        )
        self._idle_sleep_thread.start()

    def _load_idle_sleep_config(self) -> None:
        if self._brain_dir is None:
            return
        path = self._brain_dir / "settings" / "idle_sleep.json"
        try:
            if path.is_file():
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    self._idle_sleep_enabled = value.get("enabled", True) is not False
                    self._idle_sleep_seconds = max(30, min(
                        86400, int(value.get("idle_seconds", 60)),
                    ))
        except (OSError, ValueError, TypeError, UnicodeError):
            pass

    def _release_model(self) -> None:
        model = self._model
        self._model = None
        if model is not None:
            close = getattr(model, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _idle_sleep_watch(self) -> None:
        while not self._idle_sleep_stop.wait(3.0):
            if not self._idle_sleep_enabled:
                continue
            if not self._lock.acquire(blocking=False):
                self._idle_last_used = time.monotonic()
                continue
            try:
                if (self._model is not None
                        and time.monotonic() - self._idle_last_used >= self._idle_sleep_seconds):
                    self._release_model()
                    self._idle_sleep_unloads += 1
            finally:
                self._lock.release()

    def resident_status(self) -> dict:
        return {
            "backend": "native-gguf",
            "resident": self._model is not None,
            "idle_timeout_seconds": self._idle_sleep_seconds,
            "auto_sleep_enabled": self._idle_sleep_enabled,
            "auto_sleep_unloads": self._idle_sleep_unloads,
            "sleeping": self._idle_sleep_unloads > 0 and self._model is None,
        }

    def close(self) -> None:
        self._idle_sleep_stop.set()
        with self._lock:
            self._release_model()
        if self._idle_sleep_thread is not threading.current_thread():
            self._idle_sleep_thread.join(timeout=1.0)

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
        items = [m for m in messages if m.role in {"user", "assistant", "system"}]
        # A customer-facing support draft must not inherit the assistant's
        # general "Ты Леший" identity, which causes self-introductions.
        support_mode = any(
            m.role == "system" and "LESHY_SUPPORT_DRAFT_V1" in str(m.content)
            for m in items
        )
        from .mentor_context import mentor_active
        prefix = [] if support_mode or mentor_active() else [ChatMessage("system", _SYSTEM_PROMPT)]
        prepared = _normalize_system_messages(prefix + items)
        payload = [{"role": m.role, "content": m.content} for m in prepared]
        with self._lock:
            model = self._load()
            try:
                measured_start = time.monotonic()
                result = model.create_chat_completion(
                    messages=payload,
                    temperature=float(os.getenv("LESHY_TEMPERATURE", "0.25")),
                    max_tokens=int(os.getenv("LESHY_MAX_TOKENS", "1024")),
                )
                from .mentor_performance import observe_completion
                observe_completion(result, backend="native-gguf", model=self.model,
                                   duration_seconds=time.monotonic() - measured_start)
                text = result["choices"][0]["message"]["content"]
            except Exception as exc:  # pragma: no cover - зависит от backend
                raise LLMUnavailable(f"Ошибка локальной модели: {exc}") from exc
            finally:
                self._idle_last_used = time.monotonic()
        value = finalize_model_output(str(text or ""))
        if not value:
            raise LLMUnavailable("Локальная модель вернула пустой пользовательский ответ")
        return value