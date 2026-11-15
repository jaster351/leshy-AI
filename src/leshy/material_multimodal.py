from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any
from urllib import request
from xml.etree import ElementTree
from zipfile import ZipFile

from .diagnostics import event, problem
from .video_knowledge import VideoSegment


class LocalMultimodalAnalyzer:
    """Локальные адаптеры обучения по аудио, видео, документам и изображениям.

    Внешние AI API не используются. ASR выполняется faster-whisper, документы
    читаются локально, OCR на Windows использует Windows.Media.Ocr. Визуальное
    описание кадров использует локальный Ollama vision endpoint, когда он есть.
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.models_dir = self.brain_dir / "models" / "multimodal"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._whisper = None
        self._lock = threading.RLock()
        self.vision_model = os.getenv("LESHY_VISION_MODEL", "qwen2.5vl:3b").strip() or "qwen2.5vl:3b"
        self.ollama_url = os.getenv("LESHY_VISION_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

    def status(self) -> dict[str, Any]:
        result = {
            "asr": False,
            "pdf": False,
            "docx": True,
            "pptx": True,
            "ocr": os.name == "nt",
            "vision": False,
            "vision_model": self.vision_model,
            "vision_endpoint": self.ollama_url,
        }
        try:
            import faster_whisper  # noqa: F401
            result["asr"] = True
        except Exception:
            pass
        try:
            import pypdf  # noqa: F401
            result["pdf"] = True
        except Exception:
            pass
        try:
            req = request.Request(self.ollama_url + "/api/tags", method="GET")
            with request.urlopen(req, timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            names = {str(row.get("name") or "") for row in (payload.get("models") or []) if isinstance(row, dict)}
            result["vision"] = any(name == self.vision_model or name.startswith(self.vision_model + ":") for name in names)
        except Exception:
            pass
        return result

    def _whisper_model(self):
        with self._lock:
            if self._whisper is not None:
                return self._whisper
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("Локальный ASR недоступен: faster-whisper не установлен в этой сборке") from exc
            model_name = os.getenv("LESHY_ASR_MODEL", "small").strip() or "small"
            # CTranslate2 сам использует оптимальный CPU runtime. На системах с
            # поддерживаемым CUDA можно явно задать LESHY_ASR_DEVICE=cuda.
            device = os.getenv("LESHY_ASR_DEVICE", "cpu").strip() or "cpu"
            compute = os.getenv("LESHY_ASR_COMPUTE", "int8" if device == "cpu" else "float16").strip()
            self._whisper = WhisperModel(
                model_name,
                device=device,
                compute_type=compute,
                download_root=str(self.models_dir / "whisper"),
            )
            event("local_asr_loaded", model=model_name, device=device, compute_type=compute)
            return self._whisper

    def transcribe_file(self, path: Path | str) -> list[VideoSegment]:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        model = self._whisper_model()
        rows, info = model.transcribe(str(source), vad_filter=True, beam_size=3)
        result: list[VideoSegment] = []
        for row in rows:
            text = " ".join(str(getattr(row, "text", "") or "").split())
            if text:
                result.append(VideoSegment(float(row.start), float(row.end), transcript=text))
        event("local_asr_finished", source=str(source), segments=len(result), language=str(getattr(info, "language", "") or ""))
        return result

    @staticmethod
    def _download_media(url: str, directory: Path, *, audio_only: bool) -> Path:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("Для загрузки медиа требуется yt-dlp") from exc
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": str(directory / "source.%(ext)s"),
            "format": "bestaudio/best" if audio_only else "bestvideo*+bestaudio/best",
            "merge_output_format": "mkv",
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
        files = sorted((p for p in directory.iterdir() if p.is_file()), key=lambda p: p.stat().st_size, reverse=True)
        if not files:
            raise RuntimeError("Не удалось получить локальную копию материала")
        return files[0]

    def transcribe_url(self, url: str) -> list[VideoSegment]:
        with tempfile.TemporaryDirectory(prefix="leshy-asr-") as tmp:
            media = self._download_media(url, Path(tmp), audio_only=True)
            return self.transcribe_file(media)

    @staticmethod
    def _xml_text(blob: bytes) -> str:
        try:
            root = ElementTree.fromstring(blob)
        except ElementTree.ParseError:
            return ""
        parts = [" ".join(str(node.text or "").split()) for node in root.iter() if node.text and str(node.text).strip()]
        return "\n".join(part for part in parts if part)

    def extract_document(self, path: Path | str) -> str:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        suffix = source.suffix.lower()
        if suffix in {".txt", ".md"}:
            return source.read_text(encoding="utf-8", errors="replace")[:5_000_000]
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise RuntimeError("Для PDF требуется локальный пакет pypdf") from exc
            reader = PdfReader(str(source))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages)[:5_000_000]
        if suffix == ".docx":
            chunks: list[str] = []
            with ZipFile(source) as archive:
                for name in sorted(archive.namelist()):
                    if name == "word/document.xml" or name.startswith("word/header") or name.startswith("word/footer"):
                        chunks.append(self._xml_text(archive.read(name)))
            return "\n\n".join(x for x in chunks if x)[:5_000_000]
        if suffix == ".pptx":
            chunks = []
            with ZipFile(source) as archive:
                slides = sorted(
                    (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                    key=lambda value: int(re.search(r"(\d+)", value).group(1)),
                )
                for index, name in enumerate(slides, 1):
                    text = self._xml_text(archive.read(name))
                    if text:
                        chunks.append(f"Слайд {index}:\n{text}")
            return "\n\n".join(chunks)[:5_000_000]
        raise RuntimeError(f"Формат документа пока не поддерживается: {suffix}")

    @staticmethod
    def _powershell() -> str:
        return shutil.which("powershell.exe") or shutil.which("pwsh") or ""

    def ocr_image(self, path: Path | str) -> str:
        source = Path(path).resolve()
        if os.name != "nt":
            raise RuntimeError("Встроенный OCR сейчас реализован через Windows.Media.Ocr")
        powershell = self._powershell()
        if not powershell:
            raise RuntimeError("PowerShell не найден")
        script = r'''
param([string]$Path)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
function Await($op,$type){$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods()|?{$_.Name -eq 'AsTask' -and $_.IsGenericMethod}|select -First 1).MakeGenericMethod($type);$task=$asTask.Invoke($null,@($op));$task.Wait();$task.Result}
$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream=Await ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
$decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$result.Text
'''
        completed = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, "-Path", str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "OCR failed")[-2000:])
        return completed.stdout.strip()[:300_000]

    def vision_caption(self, path: Path | str, *, prompt: str = "Опиши, что происходит на изображении. Укажи важные объекты, действия и видимый текст.") -> str:
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        payload = json.dumps({
            "model": self.vision_model,
            "prompt": prompt,
            "images": [encoded],
            "stream": False,
            "options": {"temperature": 0.1},
        }, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.ollama_url + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        return " ".join(str(result.get("response") or "").split())[:10_000]

    @staticmethod
    def _ffmpeg() -> str:
        return shutil.which("ffmpeg") or "ffmpeg"

    def sample_video(self, path: Path | str, *, duration: float = 0.0, count: int = 5) -> list[dict[str, Any]]:
        source = Path(path)
        count = max(1, min(int(count), 10))
        duration = max(float(duration or 0), 30.0)
        timestamps = [duration * (i + 1) / (count + 1) for i in range(count)]
        result: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="leshy-frames-") as tmp:
            root = Path(tmp)
            for index, timestamp in enumerate(timestamps):
                frame = root / f"frame-{index}.jpg"
                command = [self._ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp:.2f}", "-i", str(source), "-frames:v", "1", str(frame)]
                try:
                    run = subprocess.run(command, capture_output=True, timeout=45)
                    if run.returncode != 0 or not frame.is_file():
                        continue
                    ocr = ""
                    caption = ""
                    try:
                        ocr = self.ocr_image(frame)
                    except Exception:
                        pass
                    try:
                        caption = self.vision_caption(frame)
                    except Exception:
                        pass
                    if ocr or caption:
                        result.append({"timestamp": timestamp, "ocr": ocr, "caption": caption})
                except Exception as exc:
                    problem("video_frame_analysis_failed", str(exc), timestamp=timestamp)
        return result

    def analyze_video_url(self, url: str, *, duration: float = 0.0) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="leshy-video-") as tmp:
            media = self._download_media(url, Path(tmp), audio_only=False)
            return self.sample_video(media, duration=duration)


__all__ = ["LocalMultimodalAnalyzer"]
