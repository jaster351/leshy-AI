from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib import request
from urllib.error import HTTPError

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
    sha256: str = ""


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
        id="qwen35-9b-original-q4km",
        title="Qwen3.5 9B (оригинальная) — Q4_K_M",
        filename="Qwen_Qwen3.5-9B-Q4_K_M.gguf",
        url="https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF/resolve/c512afe1bc55a45304dbe577088139fb9405b02e/Qwen_Qwen3.5-9B-Q4_K_M.gguf",
        quant="Q4_K_M",
        size_label="≈5,87 ГБ",
        family="Qwen3.5 9B",
        description=(
            "Оригинальная Qwen3.5 9B без сторонней модификации инструкционного поведения. "
            "Потенциальное улучшение качества по сравнению с Qwen3 8B; "
            "реальный эффект и совместимость с Vulkan проверяются после установки."
        ),
        best_for="Русский диалог, рассуждения, задачи агента и работа с инструментами.",
        memory_note=(
            "Для видеокарты с 8 ГБ VRAM возможен частичный offload при большом контексте. "
            "Сохраните Qwen3 8B для мгновенного отката; не переключать автоматически "
            "без успешной проверки модели и backend."
        ),
        runtime="modern",
        sha256="dcdc2a0977b42d9494b99671e74834027d0eaf9e925f8e03cc1880e617be1d39",
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

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _download_one(
        self, url: str, filename: str, *, stage: str, expected_sha256: str = "",
    ) -> int:
        """Download in the background with safe byte-range resume.

        The catalog owns the URL and file name; this is not an arbitrary URL
        downloader. A pinned SHA-256, when available, is mandatory before
        atomically replacing an installed GGUF.
        """
        target = self.models_dir / Path(filename).name
        expected = str(expected_sha256 or "").strip().lower()
        if expected and (len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected)):
            raise ValueError("Неправильная контрольная сумма модели")
        if target.is_file() and self._is_gguf(target):
            if not expected or hmac.compare_digest(self._file_sha256(target), expected):
                return int(target.stat().st_size)

        temporary = target.with_name(target.name + ".download")
        offset = temporary.stat().st_size if temporary.is_file() else 0
        if offset > MAX_MODEL_BYTES:
            temporary.unlink()
            offset = 0

        headers = {"User-Agent": "Leshy-AI/model-library"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        req = request.Request(url, headers=headers)
        try:
            try:
                response = request.urlopen(req, timeout=90)
            except HTTPError as exc:
                if not offset or exc.code != 416:
                    raise
                # Server says the partial range is no longer satisfiable.
                # Start a fresh GET, never treat the partial file as complete.
                temporary.unlink(missing_ok=True)
                offset = 0
                response = request.urlopen(
                    request.Request(url, headers={"User-Agent": "Leshy-AI/model-library"}),
                    timeout=90,
                )
            with response:
                status = int(getattr(response, "status", 0) or response.getcode())
                if offset and status == 206:
                    content_range = str(response.headers.get("Content-Range") or "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        temporary.unlink(missing_ok=True)
                        raise ValueError("Сервер прислал неверный диапазон продолжения загрузки")
                elif offset and status == 200:
                    # Range ignored: restart rather than append duplicate bytes.
                    offset = 0
                elif status != 200:
                    raise ValueError(f"Недопустимый HTTP-статус скачивания: {status}")

                remaining = int(response.headers.get("Content-Length") or 0)
                total = offset + remaining if remaining else 0
                if total > MAX_MODEL_BYTES:
                    raise ValueError("Удалённый GGUF превышает лимит 128 ГБ")
                self._set(state=stage, total=total, downloaded=offset, current_file=filename)
                downloaded = offset
                with temporary.open("ab" if offset else "wb") as output:
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
                if total and downloaded != total:
                    raise OSError(
                        f"Загрузка прервана: {downloaded} из {total} байт; "
                        "остаток будет скачан при повторной попытке"
                    )
            if not self._is_gguf(temporary):
                temporary.unlink(missing_ok=True)
                raise ValueError(f"Файл {filename} не содержит сигнатуру GGUF")
            if expected and not hmac.compare_digest(self._file_sha256(temporary), expected):
                temporary.unlink(missing_ok=True)
                raise ValueError("SHA-256 модели не совпал с контрольной суммой издателя")
            os.replace(temporary, target)
            return downloaded
        except Exception:
            # Preserve incomplete downloads on network errors for byte-range
            # resume; never publish an unverified .download as an installed GGUF.
            raise

    def _run(self, model: RecommendedModel, include_vision: bool) -> None:
        try:
            event("model_library_download_started", model_id=model.id, filename=model.filename)
            main_bytes = self._download_one(
                model.url, model.filename, stage="downloading_model",
                expected_sha256=model.sha256,
            )
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
