from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib import request
import uuid
import zipfile

from .diagnostics import event, problem


_SD_RELEASE = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest"
_SD_MODEL_URL = (
    "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/"
    "v1-5-pruned-emaonly.safetensors"
)
_SD_MODEL_SHA256 = "6ce0161689b3853acaa03779ec93eafe75a02f4ced659bee03f50797806fa2fa"
_SD_MODEL_SIZE = 4_265_146_304
_SD_MODEL_NAME = "v1-5-pruned-emaonly.safetensors"
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class ImageGenerationManager:
    """Локальная генерация изображений без платного API.

    Windows использует официальный stable-diffusion.cpp: сначала Vulkan, затем
    CPU. Модель и runtime живут только внутри LeshyBrain. Первый запрос может
    занять время, потому что модель загружается и проверяется автоматически.
    """

    def __init__(self, brain_dir: Path, hardware=None):
        self.brain_dir = Path(brain_dir)
        self.hardware = hardware
        self.models_dir = self.brain_dir / "models" / "image"
        self.runtime_root = self.brain_dir / "runtime" / "stable-diffusion.cpp"
        self.output_dir = self.brain_dir / "generated" / "images"
        self.logs_dir = self.brain_dir / "logs"
        for path in (self.models_dir, self.runtime_root, self.output_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.model_path = self.models_dir / _SD_MODEL_NAME
        self.model_verified_path = self.model_path.with_suffix(self.model_path.suffix + ".verified.json")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.RLock()
        self._generation_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        with self._jobs_lock:
            active = [dict(job) for job in self._jobs.values() if job.get("state") not in {"done", "error"}]
        return {
            "available": os.name == "nt",
            "model": _SD_MODEL_NAME,
            "model_path": str(self.model_path),
            "model_exists": self.model_path.is_file(),
            "model_verified": self._model_cache_valid(),
            "output_dir": str(self.output_dir),
            "active_jobs": len(active),
        }

    def enqueue(self, prompt: str, *, width: int = 512, height: int = 512, steps: int = 20) -> dict[str, Any]:
        prompt = " ".join(str(prompt or "").split()).strip()
        if not prompt:
            raise ValueError("Пустое описание изображения")
        width = self._dimension(width)
        height = self._dimension(height)
        steps = max(4, min(int(steps), 50))
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "state": "queued",
            "stage": "Ожидание",
            "progress": 0.0,
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "backend": "",
            "image_url": "",
            "image_path": "",
            "error": "",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
        threading.Thread(target=self._run_job, args=(job_id,), name=f"leshy-image-{job_id[:8]}", daemon=True).start()
        event("image_generation_queued", job_id=job_id, width=width, height=height, steps=steps)
        return dict(job)

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self._jobs_lock:
            value = self._jobs.get(str(job_id))
            return dict(value) if value else None

    def resolve_generated(self, name: str) -> Path | None:
        name = Path(str(name)).name
        if not name.lower().endswith(".png"):
            return None
        target = (self.output_dir / name).resolve()
        try:
            target.relative_to(self.output_dir.resolve())
        except ValueError:
            return None
        return target if target.is_file() else None

    @staticmethod
    def _dimension(value: int) -> int:
        value = max(256, min(int(value), 1024))
        return max(256, (value // 64) * 64)

    def _update(self, job_id: str, **changes) -> None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(changes)
            job["updated_at"] = time.time()

    def _run_job(self, job_id: str) -> None:
        with self._generation_lock:
            job = self.job(job_id)
            if not job:
                return
            try:
                if os.name != "nt":
                    raise RuntimeError("Локальная генерация изображений пока поддерживается в Windows-сборке Лешего")
                self._update(job_id, state="preparing", stage="Проверка модели", progress=1.0)
                self._ensure_model(job_id)
                self._update(job_id, state="preparing", stage="Подготовка генератора", progress=70.0)

                preferred = "vulkan" if bool(getattr(self.hardware, "gpu", False)) else "cpu"
                errors: list[str] = []
                for backend in ([preferred, "cpu"] if preferred != "cpu" else ["cpu"]):
                    try:
                        cli = self._ensure_runtime(backend, job_id)
                        self._generate(cli, backend, job_id, job)
                        event("image_generation_done", job_id=job_id, backend=backend)
                        return
                    except Exception as exc:
                        errors.append(f"{backend}: {exc}")
                        problem("image_generation_backend_failed", str(exc), job_id=job_id, backend=backend)
                raise RuntimeError("; ".join(errors[-2:]))
            except Exception as exc:
                self._update(job_id, state="error", stage="Ошибка", error=str(exc)[:1500])
                problem("image_generation_failed", str(exc), job_id=job_id)

    def _model_cache_valid(self) -> bool:
        if not self.model_path.is_file() or not self.model_verified_path.is_file():
            return False
        try:
            stat = self.model_path.stat()
            data = json.loads(self.model_verified_path.read_text(encoding="utf-8"))
            return (
                int(data.get("size", -1)) == int(stat.st_size)
                and int(data.get("mtime_ns", -1)) == int(stat.st_mtime_ns)
                and str(data.get("sha256", "")).lower() == _SD_MODEL_SHA256
            )
        except Exception:
            return False

    def _write_model_cache(self) -> None:
        stat = self.model_path.stat()
        self.model_verified_path.write_text(
            json.dumps(
                {
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                    "sha256": _SD_MODEL_SHA256,
                    "verified_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _ensure_model(self, job_id: str) -> None:
        if self._model_cache_valid():
            self._update(job_id, stage="Модель готова", progress=68.0)
            return

        if self.model_path.is_file() and self.model_path.stat().st_size == _SD_MODEL_SIZE:
            digest = sha256()
            processed = 0
            with self.model_path.open("rb") as handle:
                while True:
                    block = handle.read(8 * 1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    processed += len(block)
                    self._update(job_id, stage="Проверка модели изображения", progress=min(65.0, processed * 65.0 / _SD_MODEL_SIZE))
            if digest.hexdigest().lower() == _SD_MODEL_SHA256:
                self._write_model_cache()
                self._update(job_id, stage="Модель готова", progress=68.0)
                return
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.model_path.replace(self.model_path.with_name(self.model_path.name + f".corrupt-{stamp}"))
            self.model_verified_path.unlink(missing_ok=True)
        elif self.model_path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.model_path.replace(self.model_path.with_name(self.model_path.name + f".corrupt-{stamp}"))
            self.model_verified_path.unlink(missing_ok=True)

        temporary = self.model_path.with_suffix(self.model_path.suffix + ".download")
        temporary.unlink(missing_ok=True)
        digest = sha256()
        downloaded = 0
        req = request.Request(_SD_MODEL_URL, headers={"User-Agent": "Leshy-Image/1.10"})
        self._update(job_id, state="downloading_model", stage="Загрузка модели изображения", progress=2.0)
        try:
            with request.urlopen(req, timeout=90) as response, temporary.open("wb") as handle:
                while True:
                    block = response.read(8 * 1024 * 1024)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    downloaded += len(block)
                    self._update(
                        job_id,
                        stage=f"Загрузка модели изображения: {downloaded / 1024**3:.2f} / {_SD_MODEL_SIZE / 1024**3:.2f} ГБ",
                        progress=min(65.0, 2.0 + downloaded * 63.0 / _SD_MODEL_SIZE),
                    )
                handle.flush()
                os.fsync(handle.fileno())
            if downloaded != _SD_MODEL_SIZE:
                raise RuntimeError(f"Неверный размер модели изображения: {downloaded} вместо {_SD_MODEL_SIZE}")
            actual = digest.hexdigest().lower()
            if actual != _SD_MODEL_SHA256:
                raise RuntimeError(f"SHA-256 модели изображения не совпал: {actual}")
            temporary.replace(self.model_path)
            self._write_model_cache()
            self._update(job_id, stage="Модель изображения готова", progress=68.0)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _runtime_asset(release: dict, backend: str) -> dict:
        suffix = "-bin-win-vulkan-x64.zip" if backend == "vulkan" else "-bin-win-cpu-x64.zip"
        for asset in release.get("assets") or []:
            name = str(asset.get("name") or "").lower()
            digest = str(asset.get("digest") or "")
            if name.endswith(suffix) and str(asset.get("browser_download_url") or "") and digest.startswith("sha256:"):
                return asset
        raise RuntimeError(f"Не найдена официальная Windows-сборка stable-diffusion.cpp для {backend}")

    def _runtime_cli(self, backend: str) -> Path | None:
        root = self.runtime_root / backend
        if not root.exists():
            return None
        matches = list(root.rglob("sd-cli.exe"))
        return matches[0] if matches else None

    def _ensure_runtime(self, backend: str, job_id: str) -> Path:
        existing = self._runtime_cli(backend)
        if existing:
            return existing
        self._update(job_id, stage=f"Загрузка генератора изображений ({backend})", progress=71.0)
        req = request.Request(_SD_RELEASE, headers={"Accept": "application/vnd.github+json", "User-Agent": "Leshy-Image/1.10"})
        with request.urlopen(req, timeout=30) as response:
            release = json.loads(response.read().decode("utf-8"))
        asset = self._runtime_asset(release, backend)
        expected = str(asset["digest"]).split(":", 1)[1].lower()
        with tempfile.TemporaryDirectory(prefix="leshy-sd-") as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / str(asset["name"])
            digest = sha256()
            with request.urlopen(
                request.Request(str(asset["browser_download_url"]), headers={"User-Agent": "Leshy-Image/1.10"}),
                timeout=90,
            ) as response, archive.open("wb") as handle:
                while True:
                    block = response.read(4 * 1024 * 1024)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
            if digest.hexdigest().lower() != expected:
                raise RuntimeError("SHA-256 runtime stable-diffusion.cpp не совпал")
            unpacked = tmp_path / "unpacked"
            unpacked.mkdir()
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(unpacked)
            if not list(unpacked.rglob("sd-cli.exe")):
                raise RuntimeError("В архиве stable-diffusion.cpp отсутствует sd-cli.exe")
            destination = self.runtime_root / backend
            staged = destination.with_name(destination.name + ".staging")
            shutil.rmtree(staged, ignore_errors=True)
            shutil.copytree(unpacked, staged)
            (staged / "leshy-runtime.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "tag": release.get("tag_name"),
                        "asset": asset.get("name"),
                        "sha256": expected,
                        "source": asset.get("browser_download_url"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            shutil.rmtree(destination, ignore_errors=True)
            os.replace(staged, destination)
        result = self._runtime_cli(backend)
        if not result:
            raise RuntimeError("sd-cli.exe не найден после установки runtime")
        return result

    def _generate(self, cli: Path, backend: str, job_id: str, job: dict[str, Any]) -> None:
        output = self.output_dir / f"{job_id}.png"
        output.unlink(missing_ok=True)
        seed = random.SystemRandom().randint(1, 2_147_483_647)
        args = [
            str(cli),
            "-m", str(self.model_path),
            "-p", str(job["prompt"]),
            "-o", str(output),
            "-W", str(job["width"]),
            "-H", str(job["height"]),
            "--steps", str(job["steps"]),
            "--cfg-scale", "7.0",
            "--seed", str(seed),
            "--log-level", "info",
        ]
        if backend == "vulkan":
            args += ["--backend", "diffusion=vulkan0,te=cpu,vae=cpu", "--max-vram", "vulkan0=6"]
        else:
            args += ["--backend", "cpu"]
        log_path = self.logs_dir / "image-generation.log"
        self._update(job_id, state="generating", stage=f"Генерация изображения ({backend})", progress=78.0, backend=backend)
        with log_path.open("ab") as log:
            log.write((f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] job={job_id} backend={backend}\n").encode("utf-8"))
            completed = subprocess.run(
                args,
                cwd=str(cli.parent),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1800,
                creationflags=_CREATE_NO_WINDOW,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(f"stable-diffusion.cpp завершился с кодом {completed.returncode}; см. {log_path}")
        if not output.is_file() or output.stat().st_size < 1024:
            raise RuntimeError(f"Генератор не создал корректный PNG; см. {log_path}")
        self._update(
            job_id,
            state="done",
            stage="Готово",
            progress=100.0,
            backend=backend,
            image_path=str(output),
            image_url="/generated/" + output.name,
            seed=seed,
        )


__all__ = ["ImageGenerationManager"]
