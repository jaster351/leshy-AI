from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib import request

from .diagnostics import event, problem


MAX_MODEL_BYTES = 128 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    id: str
    title: str
    filename: str
    url: str
    quant: str
    size_label: str
    family: str
    description: str
    best_for: str
    memory_note: str
    runtime: str = "auto"
    vision: bool = False
    mmproj_filename: str = ""
    mmproj_url: str = ""


RECOMMENDED_MODELS: tuple[RecommendedModel, ...] = (
    RecommendedModel(
        id="qwen3-8b-q4km",
        title="Qwen3 8B — Q4_K_M",
        filename="Qwen3-8B-Q4_K_M.gguf",
        url="https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
        quant="Q4_K_M",
        size_label="≈4,7–5,0 ГБ",
        family="Qwen3 8B",
        description="Проверенная базовая модель Лешего. Хороший баланс скорости, памяти и качества; полезна как совместимый запасной вариант.",
        best_for="Обычный чат, инструменты, поиск, RAG и работа на 16 ГБ RAM без чрезмерного pagefile.",
        memory_note="Рекомендуется как стабильный legacy/fallback вариант.",
        runtime="legacy-compatible",
    ),
    RecommendedModel(
        id="qwen35-9b-uncensored-q4km",
        title="Qwen3.5 9B Uncensored Aggressive — Q4_K_M",
        filename="Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
        url="https://huggingface.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
        quant="Q4_K_M",
        size_label="≈5,3 ГБ",
        family="Qwen3.5 9B",
        description="Современная мультимодальная Qwen3.5 с ослабленными встроенными отказами. Оптимальный вариант из этой линейки для Ваших 16 ГБ RAM.",
        best_for="Основной ассистент, свободный диалог, изображения/видео с mmproj, инструменты и длительный контекст.",
        memory_note="Рекомендуемый вариант: оставляет запас RAM для runtime, KV-cache и фоновых задач.",
        runtime="modern",
        vision=True,
        mmproj_filename="mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf",
        mmproj_url="https://huggingface.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive/resolve/main/mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf",
    ),
    RecommendedModel(
        id="qwen35-9b-uncensored-q6k",
        title="Qwen3.5 9B Uncensored Aggressive — Q6_K",
        filename="Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf",
        url="https://huggingface.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf",
        quant="Q6_K",
        size_label="≈6,9 ГБ",
        family="Qwen3.5 9B",
        description="Та же Qwen3.5 9B, но с более высокой точностью весов, чем Q4_K_M. Качество немного выше ценой большей памяти.",
        best_for="Когда важнее качество ответа, чем скорость и экономия RAM; vision/video доступны с тем же mmproj.",
        memory_note="На 16 ГБ RAM рабочий вариант, но запас для большого контекста заметно меньше.",
        runtime="modern",
        vision=True,
        mmproj_filename="mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf",
        mmproj_url="https://huggingface.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive/resolve/main/mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf",
    ),
    RecommendedModel(
        id="qwen35-9b-uncensored-q8",
        title="Qwen3.5 9B Uncensored Aggressive — Q8_0",
        filename="Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf",
        url="https://huggingface.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf",
        quant="Q8_0",
        size_label="≈8,9 ГБ",
        family="Qwen3.5 9B",
        description="Высококачественная квантизация Qwen3.5 9B с минимальной потерей качества относительно более лёгких вариантов.",
        best_for="Максимальное качество 9B-модели, если допускается активное использование RAM/pagefile.",
        memory_note="Для 16 ГБ RAM тяжёлая: возможен pagefile, небольшой контекст и более низкая скорость.",
        runtime="modern",
        vision=True,
        mmproj_filename="mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf",
        mmproj_url="https://huggingface.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive/resolve/main/mmproj-Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-BF16.gguf",
    ),
    RecommendedModel(
        id="qwen38-27b-abliterated-q3km",
        title="Qwen3.8 27B Abliterated — Q3_K_M",
        filename="qwen3.8-27b-abliterated-q3_k_m.gguf",
        url="https://huggingface.co/dachans/Qwen3.8-27B-abliterated-GGUF/resolve/main/qwen3.8-27b-abliterated-q3_k_m.gguf",
        quant="Q3_K_M",
        size_label="≈13 ГБ",
        family="Qwen3.8 27B",
        description="Значительно более крупная модель Qwen3.8 27B с удалённым направлением отказов. Сильнее 9B в рассуждениях, но намного тяжелее.",
        best_for="Сложные рассуждения, большие тексты и задачи, где качество важнее скорости.",
        memory_note="На 16 ГБ RAM требует pagefile и будет заметно медленнее; Q3_K_M выбран как наиболее реалистичный 27B вариант.",
        runtime="modern",
    ),
    RecommendedModel(
        id="qwen38-27b-abliterated-q4km",
        title="Qwen3.8 27B Abliterated — Q4_K_M",
        filename="qwen3.8-27b-abliterated-q4_k_m.gguf",
        url="https://huggingface.co/dachans/Qwen3.8-27B-abliterated-GGUF/resolve/main/qwen3.8-27b-abliterated-q4_k_m.gguf",
        quant="Q4_K_M",
        size_label="≈16 ГБ",
        family="Qwen3.8 27B",
        description="Более качественная Q4_K_M-квантизация Qwen3.8 27B. Качество выше Q3, но сама модель почти занимает всю физическую RAM Вашего ПК.",
        best_for="Максимальное локальное качество из перечисленных вариантов, когда скорость вторична.",
        memory_note="Для 16 ГБ RAM практически обязательно использование pagefile; большой контекст может быть очень тяжёлым.",
        runtime="modern",
    ),
)


class ModelLibraryManager:
    """Каталог рекомендуемых GGUF с фоновой загрузкой и проверкой заголовка."""

    def __init__(self, models_dir: Path | str):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._job: dict[str, Any] = {"state": "idle", "model_id": "", "downloaded": 0, "total": 0, "error": ""}

    @staticmethod
    def _entry(model_id: str) -> RecommendedModel:
        for item in RECOMMENDED_MODELS:
            if item.id == str(model_id):
                return item
        raise KeyError("Неизвестная модель каталога")

    @staticmethod
    def _is_gguf(path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return handle.read(4) == b"GGUF"
        except OSError:
            return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            job = dict(self._job)
        items: list[dict[str, Any]] = []
        for model in RECOMMENDED_MODELS:
            row = asdict(model)
            main = self.models_dir / model.filename
            row["installed"] = main.is_file() and self._is_gguf(main)
            row["installed_bytes"] = main.stat().st_size if main.is_file() else 0
            if model.mmproj_filename:
                vision = self.models_dir / model.mmproj_filename
                row["mmproj_installed"] = vision.is_file() and self._is_gguf(vision)
            else:
                row["mmproj_installed"] = False
            items.append(row)
        return {"models_dir": str(self.models_dir), "items": items, "job": job}

    def download(self, model_id: str, *, include_vision: bool = True) -> dict[str, Any]:
        model = self._entry(model_id)
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._job = {"state": "queued", "model_id": model.id, "downloaded": 0, "total": 0, "error": "", "include_vision": bool(include_vision)}
            self._thread = threading.Thread(
                target=self._run,
                args=(model, bool(include_vision)),
                name="leshy-model-library-download",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def _set(self, **values: Any) -> None:
        with self._lock:
            self._job.update(values)

    def _download_one(self, url: str, filename: str, *, stage: str) -> int:
        target = self.models_dir / Path(filename).name
        if target.is_file() and self._is_gguf(target):
            return int(target.stat().st_size)
        temporary = target.with_name(target.name + ".download")
        downloaded = 0
        req = request.Request(url, headers={"User-Agent": "Leshy-AI/model-library"})
        try:
            with request.urlopen(req, timeout=90) as response, temporary.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                if total > MAX_MODEL_BYTES:
                    raise ValueError("Удалённый GGUF превышает лимит 128 ГБ")
                self._set(state=stage, total=total, downloaded=0, current_file=filename)
                while True:
                    chunk = response.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_MODEL_BYTES:
                        raise ValueError("GGUF превысил лимит 128 ГБ")
                    output.write(chunk)
                    self._set(downloaded=downloaded)
                output.flush()
                os.fsync(output.fileno())
            if not self._is_gguf(temporary):
                raise ValueError(f"Файл {filename} не содержит сигнатуру GGUF")
            os.replace(temporary, target)
            return downloaded
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _run(self, model: RecommendedModel, include_vision: bool) -> None:
        try:
            event("model_library_download_started", model_id=model.id, filename=model.filename)
            main_bytes = self._download_one(model.url, model.filename, stage="downloading_model")
            vision_bytes = 0
            if include_vision and model.vision and model.mmproj_url and model.mmproj_filename:
                vision_bytes = self._download_one(model.mmproj_url, model.mmproj_filename, stage="downloading_vision")
            self._set(
                state="ready",
                downloaded=main_bytes + vision_bytes,
                total=main_bytes + vision_bytes,
                error="",
                current_file="",
                finished_at=time.time(),
            )
            event("model_library_download_finished", model_id=model.id, filename=model.filename, bytes=main_bytes, vision_bytes=vision_bytes)
        except Exception as exc:
            self._set(state="error", error=str(exc)[:1000], finished_at=time.time())
            problem("model_library_download_failed", str(exc), model_id=model.id)


__all__ = ["ModelLibraryManager", "RecommendedModel", "RECOMMENDED_MODELS"]
