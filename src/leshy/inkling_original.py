"""Optional original (non-GGUF) Inkling 2 Mini checkpoint and SSD offload runtime.

The original weights remain separate from Leshy's GGUF catalog. Downloading
never switches the active model; activation requires an explicit backend choice.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import threading

from .llm import ChatMessage, LLMUnavailable, finalize_model_output

REPOSITORY = "thinkingmachines/Inkling-2-Mini"
FOLDER_NAME = "Inkling-2-Mini-original"
MANIFEST_NAME = ".leshy-original.json"


def model_directory(brain_dir: Path) -> Path:
    """On an SSD, set LESHY_INKLING_MODEL_DIR to the desired directory."""
    custom = os.environ.get("LESHY_INKLING_MODEL_DIR", "").strip()
    return Path(custom).expanduser().resolve() if custom else Path(brain_dir).resolve() / "models" / FOLDER_NAME


def offload_directory(brain_dir: Path) -> Path:
    """SSD staging is separate from weights and disposable when not running."""
    custom = os.environ.get("LESHY_SSD_OFFLOAD_DIR", "").strip()
    return Path(custom).expanduser().resolve() if custom else Path(brain_dir).resolve() / "offload" / FOLDER_NAME


def _is_checkpoint(target: Path) -> bool:
    return (
        (target / "config.json").is_file()
        and (target / "tokenizer_config.json").is_file()
        and any(target.glob("*.safetensors"))
    )


def checkpoint_status(brain_dir: Path) -> dict:
    target = model_directory(brain_dir)
    result = {
        "repository": REPOSITORY,
        "path": str(target),
        "offload_path": str(offload_directory(brain_dir)),
        "state": "missing",
        "activated": False,
    }
    manifest = target / MANIFEST_NAME
    if not manifest.is_file():
        if target.exists():
            result["state"] = "incomplete"
        return result
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        files = data["files"]
        if data["repository"] != REPOSITORY or not data["revision"] or not isinstance(files, list) or not files:
            raise ValueError("Некорректный манифест оригинала")
        total = 0
        for item in files:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("Небезопасное имя файла в манифесте")
            file = (target / relative).resolve()
            if not file.is_relative_to(target.resolve()) or not file.is_file() or file.stat().st_size != int(item["bytes"]):
                raise ValueError(f"Отсутствует или повреждён файл: {relative}")
            total += int(item["bytes"])
        if not _is_checkpoint(target):
            raise ValueError("Отсутствуют обязательные файлы модели")
        result.update(state="downloaded", revision=data["revision"], files=len(files), bytes=total)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        result.update(state="incomplete", error=str(exc))
    return result


def download_checkpoint(brain_dir: Path) -> dict:
    """Snapshot resumes interrupted Xet downloads and pins a single HF revision."""
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as exc:
        raise RuntimeError("Требуются huggingface_hub и hf_xet (установите зависимости Лешего)") from exc
    from .hf_settings import HuggingFaceSettings

    brain_dir = Path(brain_dir).resolve()
    HuggingFaceSettings(brain_dir).apply()
    target = model_directory(brain_dir)
    target.mkdir(parents=True, exist_ok=True)
    info = HfApi().model_info(repo_id=REPOSITORY, files_metadata=True)
    revision = str(info.sha or "")
    if not revision:
        raise RuntimeError("Не удалось получить ревизию модели с Hugging Face")
    siblings = list(info.siblings or [])
    expected = sum(int(f.size or 0) for f in siblings)
    free = shutil.disk_usage(target).free
    existing = sum(
        f.stat().st_size for f in target.rglob("*")
        if f.is_file() and ".cache" not in f.relative_to(target).parts and f.name != MANIFEST_NAME
    )
    # Download may require extra staging space; never fill the system disk silently.
    if expected and free + existing < expected + 2 * 1024 ** 3:
        raise RuntimeError(
            f"На диске недостаточно свободного места: свободно {free // 1024 ** 3} ГиБ; "
            f"модель ~{expected // 1024 ** 3} ГиБ. Выберите SSD через LESHY_INKLING_MODEL_DIR."
        )
    snapshot_download(repo_id=REPOSITORY, revision=revision, local_dir=str(target), token=os.getenv("HF_TOKEN") or None)
    files: list[dict] = []
    for file in sorted(target.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(target)
        if ".cache" in relative.parts or file.name == MANIFEST_NAME:
            continue
        files.append({"path": relative.as_posix(), "bytes": file.stat().st_size})
    if not _is_checkpoint(target):
        raise RuntimeError("Загрузка неполная: отсутствуют обязательные файлы оригинального checkpoint")
    # Atomic marker: interruptions never mark a partial snapshot as complete.
    manifest = target / MANIFEST_NAME
    temp = target / (MANIFEST_NAME + ".tmp")
    temp.write_text(
        json.dumps({"repository": REPOSITORY, "revision": revision, "files": files}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, manifest)
    return checkpoint_status(brain_dir)


def memory_limits(torch) -> dict:
    """Bound the original Transformers/Accelerate device map before using disk."""
    ram_gib = max(2, min(1024, int(os.getenv("LESHY_INKLING_RAM_GIB", "8"))))
    limits: dict = {"cpu": f"{ram_gib}GiB"}
    if torch.cuda.is_available():
        vram_gib = torch.cuda.get_device_properties(0).total_memory // (1024 ** 3)
        configured = int(os.getenv("LESHY_INKLING_VRAM_GIB", "0"))
        # Leave room for CUDA runtime and activations.
        budget = min(configured if configured > 0 else max(1, vram_gib - 2), max(1, vram_gib - 1))
        limits[0] = f"{budget}GiB"
    return limits


class InklingOriginalLLM:
    """Opt-in HF inference, using Accelerate GPU -> RAM -> NVMe offload.

    Note: generic layer/tensor offload is NOT InferSSD expert-level prefetch.
    Native NVFP4/MXFP8 support is hardware-/backend-dependent.
    """
    model = REPOSITORY
    n_ctx = 8192
    max_tokens = 1024

    def __init__(self, brain_dir: Path):
        self.brain_dir = Path(brain_dir)
        self._lock = threading.RLock()
        self._model = None
        self._processor = None
        self._torch = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        state = checkpoint_status(self.brain_dir)
        if state["state"] != "downloaded":
            raise LLMUnavailable(
                "Оригинал Inkling 2 Mini не загружен полностью. "
                "Сначала выполните python -m leshy.inkling_original download"
            )
        try:
            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor
            import accelerate  # noqa: F401
        except (ImportError, AttributeError) as exc:
            raise LLMUnavailable(
                "Нужен отдельный Transformers-движок: установите torch, transformers и accelerate. "
                "Обычная сборка Лешего их не содержит."
            ) from exc
        target = model_directory(self.brain_dir)
        ssd = offload_directory(self.brain_dir)
        ssd.mkdir(parents=True, exist_ok=True)
        try:
            processor = AutoProcessor.from_pretrained(str(target), local_files_only=True)
            model = AutoModelForMultimodalLM.from_pretrained(
                str(target),
                local_files_only=True,
                device_map="auto",
                max_memory=memory_limits(torch),
                offload_folder=str(ssd),
                offload_state_dict=True,
            )
        except Exception as exc:
            raise LLMUnavailable(
                f"Оригинальный checkpoint не запустился с SSD offload ({type(exc).__name__}: {exc}). "
                "Проверьте поддержку NVFP4/MXFP8 и достаточность SSD/RAM/VRAM."
            ) from exc
        model.eval()
        self._torch, self._processor, self._model = torch, processor, model

    def chat(self, messages: list[ChatMessage]) -> str:
        with self._lock:
            self._ensure_loaded()
            conversation = [
                {"role": message.role, "content": [{"type": "text", "text": str(message.content)}]}
                for message in messages if message.role in {"system", "user", "assistant"}
            ]
            if not conversation:
                raise LLMUnavailable("Нет сообщений для модели")
            try:
                values = self._processor.apply_chat_template(
                    conversation, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt",
                    reasoning_effort="low",
                )
                # Accelerate dispatches tensors/layers; inputs go to the first execution device.
                execution = next(
                    (device for device in self._model.hf_device_map.values() if device != "disk"),
                    "cpu",
                )
                if isinstance(execution, int):
                    execution = f"cuda:{execution}"
                values = values.to(execution)
                with self._torch.inference_mode():
                    generated = self._model.generate(**values, max_new_tokens=self.max_tokens)
                prompt_len = values["input_ids"].shape[-1]
                result = self._processor.decode(generated[0][prompt_len:], skip_special_tokens=True)
                result = finalize_model_output(result)
                if not result:
                    raise LLMUnavailable("Модель не вернула текстового ответа")
                return result
            except LLMUnavailable:
                raise
            except Exception as exc:
                raise LLMUnavailable(f"Ошибка Inkling 2 Mini: {type(exc).__name__}: {exc}") from exc

    def resident_status(self) -> dict:
        return {
            "loaded": self._model is not None,
            "model_path": str(model_directory(self.brain_dir)),
            "ssd_offload_path": str(offload_directory(self.brain_dir)),
            "checkpoint": checkpoint_status(self.brain_dir)["state"],
            "backend": "transformers-accelerate",
        }

    def close(self) -> None:
        with self._lock:
            self._model = None
            self._processor = None
            if self._torch is not None and self._torch.cuda.is_available():
                self._torch.cuda.empty_cache()
            self._torch = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Leshy: original Inkling 2 Mini and SSD offload")
    parser.add_argument("action", choices=("download", "status", "ask"))
    parser.add_argument("--brain-dir", default=os.getenv("LESHY_BRAIN_DIR") or str(Path.cwd() / "LeshyBrain"))
    parser.add_argument("--question", default="Как Вас зовут?")
    args = parser.parse_args()
    brain = Path(args.brain_dir)
    if args.action == "download":
        result = download_checkpoint(brain)
    elif args.action == "status":
        result = checkpoint_status(brain)
    else:
        engine = InklingOriginalLLM(brain)
        try:
            result = {"reply": engine.chat([ChatMessage("user", args.question)])}
        finally:
            engine.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
