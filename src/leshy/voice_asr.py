from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from .diagnostics import event, problem


@dataclass(slots=True)
class VoiceTranscript:
    text: str
    language: str | None
    language_probability: float | None
    duration_seconds: float | None
    latency_ms: int
    model: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "language_probability": self.language_probability,
            "duration_seconds": self.duration_seconds,
            "latency_ms": self.latency_ms,
            "model": self.model,
        }


class LocalVoiceASR:
    """Локальное распознавание микрофона без Web Speech API и внешних speech-сервисов.

    Браузер только захватывает аудио через getUserMedia/MediaRecorder. Само
    распознавание выполняется здесь с помощью faster-whisper. Модель загружается
    лениво при первом голосовом запросе и кэшируется в LeshyBrain/models/asr.
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.root = self.brain_dir / "voice"
        self.model_root = self.brain_dir / "models" / "asr"
        self.temp_root = self.root / "tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.model_name = os.getenv("LESHY_ASR_MODEL", "small").strip() or "small"
        self.device = os.getenv("LESHY_ASR_DEVICE", "cpu").strip().lower() or "cpu"
        self.compute_type = os.getenv("LESHY_ASR_COMPUTE", "int8").strip() or "int8"
        self._model = None
        self._model_lock = threading.RLock()
        self._transcribe_lock = threading.RLock()
        self._last_error = ""
        self._loaded_at: float | None = None

    def _load_model(self):
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:  # pragma: no cover - runtime packaging path
                self._last_error = str(exc)
                raise RuntimeError(
                    "Локальный ASR faster-whisper недоступен в этой сборке. "
                    "Нужно пересобрать Лешего с voice-зависимостями."
                ) from exc

            started = time.monotonic()
            event(
                "voice_asr_model_loading",
                model=self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(self.model_root),
            )
            try:
                self._model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(self.model_root),
                )
            except Exception as exc:
                # На CPU int8 является самым совместимым вариантом. Если пользователь
                # указал неподдерживаемый compute type, один раз откатываемся на int8.
                if self.device == "cpu" and self.compute_type != "int8":
                    self.compute_type = "int8"
                    self._model = WhisperModel(
                        self.model_name,
                        device="cpu",
                        compute_type="int8",
                        download_root=str(self.model_root),
                    )
                else:
                    self._last_error = str(exc)
                    problem("voice_asr_model_load_failed", str(exc), model=self.model_name)
                    raise RuntimeError(f"Не удалось загрузить локальную ASR-модель {self.model_name}: {exc}") from exc
            self._loaded_at = time.time()
            self._last_error = ""
            event(
                "voice_asr_model_ready",
                model=self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return self._model

    @staticmethod
    def _suffix(content_type: str) -> str:
        value = (content_type or "").lower()
        if "ogg" in value or "opus" in value and "webm" not in value:
            return ".ogg"
        if "wav" in value:
            return ".wav"
        if "mp4" in value or "m4a" in value:
            return ".m4a"
        return ".webm"

    def transcribe(self, audio: bytes, *, content_type: str = "audio/webm", language: str | None = None) -> VoiceTranscript:
        if not audio:
            raise ValueError("Пустая аудиозапись")
        if len(audio) > 24 * 1024 * 1024:
            raise ValueError("Голосовая запись слишком большая (максимум 24 МБ)")

        model = self._load_model()
        started = time.monotonic()
        temp_path: Path | None = None
        try:
            fd, raw_path = tempfile.mkstemp(prefix="leshy-voice-", suffix=self._suffix(content_type), dir=self.temp_root)
            os.close(fd)
            temp_path = Path(raw_path)
            temp_path.write_bytes(audio)
            with self._transcribe_lock:
                segments, info = model.transcribe(
                    str(temp_path),
                    language=(language or None),
                    beam_size=3,
                    best_of=3,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 350},
                    condition_on_previous_text=True,
                    word_timestamps=False,
                    temperature=0.0,
                )
                text = " ".join(str(segment.text or "").strip() for segment in segments).strip()
            latency_ms = int((time.monotonic() - started) * 1000)
            transcript = VoiceTranscript(
                text=text,
                language=str(getattr(info, "language", "") or "") or None,
                language_probability=(float(getattr(info, "language_probability", 0.0)) if getattr(info, "language_probability", None) is not None else None),
                duration_seconds=(float(getattr(info, "duration", 0.0)) if getattr(info, "duration", None) is not None else None),
                latency_ms=latency_ms,
                model=self.model_name,
            )
            event(
                "voice_asr_transcribed",
                model=self.model_name,
                audio_bytes=len(audio),
                chars=len(text),
                latency_ms=latency_ms,
                language=transcript.language,
            )
            return transcript
        except Exception as exc:
            self._last_error = str(exc)
            problem("voice_asr_transcribe_failed", str(exc), model=self.model_name, audio_bytes=len(audio))
            raise RuntimeError(f"Не удалось распознать речь локально: {exc}") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def status(self) -> dict[str, Any]:
        return {
            "backend": "faster-whisper-local",
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "loaded": self._model is not None,
            "loaded_at": self._loaded_at,
            "model_dir": str(self.model_root),
            "uses_browser_speech_api": False,
            "last_error": self._last_error,
        }


__all__ = ["LocalVoiceASR", "VoiceTranscript"]
