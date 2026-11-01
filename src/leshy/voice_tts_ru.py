from __future__ import annotations

from pathlib import Path
import os
import subprocess
import tempfile
import time
from typing import Any

from .diagnostics import event, problem
from .voice_tts import LocalVoiceTTS, _PIPER_VOICES, _PROFILE_RATE


class RussianLocalVoiceTTS(LocalVoiceTTS):
    """Русскоязычная безопасная обвязка локального TTS.

    Не допускает молчаливого выбора нерусского SAPI-голоса и всегда передаёт
    кириллицу внешним движкам в UTF-8. Это особенно важно на Windows, где
    системная кодировка консоли может отличаться от UTF-8.
    """

    LANGUAGE = "ru-RU"

    @staticmethod
    def _is_russian_language(value: object) -> bool:
        language = str(value or "").strip().lower().replace("_", "-")
        return language == "ru" or language.startswith("ru-")

    def _russian_system_voices(self) -> list[dict[str, str]]:
        return [item for item in self._system_voices() if self._is_russian_language(item.get("language"))]

    def _valid_russian_system_voice(self, voice_id: str, gender: str = "") -> str:
        requested = str(voice_id or "").strip()
        wanted_gender = str(gender or "").strip().lower()
        voices = self._russian_system_voices()
        if requested:
            for item in voices:
                if str(item.get("id") or "") == requested:
                    return requested
        if wanted_gender:
            for item in voices:
                if str(item.get("gender") or "").strip().lower() == wanted_gender:
                    return str(item.get("id") or "")
        return str((voices[0] if voices else {}).get("id") or "")

    def _piper_voice_installed(self, voice_id: str) -> bool:
        if voice_id not in _PIPER_VOICES or not str(voice_id).startswith("ru_RU-"):
            return False
        model, config = self._piper_voice_paths(voice_id)
        return model.is_file() and config.is_file()

    def _synthesize_windows(self, value: str, pref, preferred_voice: str, out_path: Path) -> str:
        if os.name != "nt" or not self._powershell:
            raise RuntimeError("Windows System Speech недоступен")
        gender = "female" if pref.effective_gender == "female" else "male"
        requested = str(preferred_voice or pref.voice_id or "").strip()
        voice = self._valid_russian_system_voice(requested, gender)
        if not voice:
            raise RuntimeError("В Windows не найден ни один русский голос SAPI. Установите русский голос в параметрах Windows.")

        rate = int(round((self._effective_rate(pref) - 1.0) * 10))
        script = r'''param(
  [Parameter(Mandatory=$true)][string]$TextPath,
  [Parameter(Mandatory=$true)][string]$OutPath,
  [Parameter(Mandatory=$true)][string]$PreferredVoice,
  [int]$Rate = 0
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voices = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo })
  $selected = $voices | Where-Object { $_.Name -eq $PreferredVoice -and $_.Culture.Name -like 'ru-*' } | Select-Object -First 1
  if (-not $selected) { throw 'Русский голос SAPI не найден' }
  $synth.SelectVoice($selected.Name)
  $text = [System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8)
  $synth.Rate = [Math]::Max(-10,[Math]::Min(10,$Rate))
  $synth.Volume = 100
  $synth.SetOutputToWaveFile($OutPath)
  $synth.Speak($text)
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  [Console]::Out.Write($selected.Name)
} finally {
  try { $synth.Dispose() } catch { }
}
'''
        with tempfile.TemporaryDirectory(prefix="leshy-ru-sapi-", dir=self.temp_root) as tmp:
            text_path = Path(tmp) / "speech.txt"
            script_path = Path(tmp) / "speak.ps1"
            text_path.write_text(value, encoding="utf-8")
            script_path.write_text(script, encoding="utf-8")
            cmd = [
                str(self._powershell), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script_path), "-TextPath", str(text_path),
                "-OutPath", str(out_path), "-PreferredVoice", voice, "-Rate", str(rate),
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                creationflags=(0x08000000 if os.name == "nt" else 0),
            )
            if proc.returncode != 0 or not out_path.is_file():
                raise RuntimeError((proc.stderr or proc.stdout or f"exit={proc.returncode}").strip())
        return voice

    def _synthesize_piper(self, value: str, pref, out_path: Path) -> str:
        exe = self._piper_executable()
        if exe is None:
            raise RuntimeError("Piper ещё не установлен. Нажмите «Скачать / подключить» в настройках голоса.")
        voice_id = pref.voice_id if pref.voice_id in _PIPER_VOICES else self._default_piper_voice(pref.effective_gender)
        if not str(voice_id).startswith("ru_RU-"):
            voice_id = self._default_piper_voice(pref.effective_gender)
        model, config = self._piper_voice_paths(voice_id)
        if not model.is_file() or not config.is_file():
            raise RuntimeError(f"Русский голос {voice_id} не загружен. Нажмите «Скачать / подключить».")
        length_scale = 1.0 / self._effective_rate(pref)
        cmd = [
            str(exe), "--model", str(model), "--config", str(config),
            "--output_file", str(out_path), "--length_scale", f"{length_scale:.3f}",
        ]
        # Piper ожидает UTF-8 в stdin. Передача через text=True на Windows могла
        # использовать локальную OEM/ANSI-кодировку и превращать кириллицу в мусор.
        proc = subprocess.run(
            cmd,
            input=(value + "\n").encode("utf-8"),
            capture_output=True,
            timeout=120,
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
        if proc.returncode != 0 or not out_path.is_file():
            stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
            stdout = (proc.stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(stderr or stdout or f"exit={proc.returncode}")
        return voice_id

    def _synthesize_espeak(self, value: str, pref, out_path: Path) -> str:
        exe = self._espeak_executable()
        if exe is None:
            raise RuntimeError("eSpeak NG не установлен")
        variant = "f3" if pref.effective_gender == "female" else "m3"
        speed = max(100, min(260, int(175 * self._effective_rate(pref))))
        with tempfile.TemporaryDirectory(prefix="leshy-espeak-ru-", dir=self.temp_root) as tmp:
            text_path = Path(tmp) / "speech.txt"
            text_path.write_text(value, encoding="utf-8")
            proc = subprocess.run(
                [str(exe), "-b", "1", "-v", f"ru+{variant}", "-s", str(speed), "-f", str(text_path), "-w", str(out_path)],
                capture_output=True,
                timeout=90,
                creationflags=(0x08000000 if os.name == "nt" else 0),
            )
            if proc.returncode != 0 or not out_path.is_file():
                stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
                stdout = (proc.stdout or b"").decode("utf-8", "replace").strip()
                raise RuntimeError(stderr or stdout or f"exit={proc.returncode}")
        return f"eSpeak NG ru+{variant}"

    def configure(self, **values: Any) -> dict[str, Any]:
        engine = str(values.get("engine") or self.preferences.load().effective_engine).strip().lower()
        gender = str(values.get("selected_gender") or self.preferences.load().effective_gender).strip().lower()
        voice_id = str(values.get("voice_id") or "").strip()

        # Не переносим ID голоса между несовместимыми движками.
        if engine == "piper":
            if voice_id not in _PIPER_VOICES or not voice_id.startswith("ru_RU-"):
                voice_id = ""
        elif engine in {"windows", "rhvoice"}:
            voice_id = self._valid_russian_system_voice(voice_id, gender)
        elif engine == "espeak":
            voice_id = ""
        elif engine == "auto":
            if voice_id and voice_id not in _PIPER_VOICES and not self._valid_russian_system_voice(voice_id, gender):
                voice_id = ""

        pref = self.preferences.save(
            values.get("selected_gender"),
            engine=engine,
            voice_id=voice_id,
            profile=values.get("profile"),
            rate=values.get("rate"),
        )
        event("voice_preferences_saved", language=self.LANGUAGE, **pref.as_dict())
        return self.status()

    def synthesize(self, text: str, *, preferred_voice: str = "") -> tuple[bytes, dict[str, Any]]:
        value = str(text or "").strip()
        if not value:
            raise ValueError("Текст для озвучивания пуст")
        if len(value) > 20000:
            value = value[:20000]
        pref = self.preferences.load()
        engine = pref.effective_engine
        selected_piper = pref.voice_id if pref.voice_id in _PIPER_VOICES else self._default_piper_voice(pref.effective_gender)

        if engine == "auto":
            if self._piper_executable() is not None and self._piper_voice_installed(selected_piper):
                engine = "piper"
            elif self._valid_russian_system_voice("", pref.effective_gender):
                engine = "windows"
            elif self._espeak_executable() is not None:
                engine = "espeak"
            else:
                raise RuntimeError("Не найден ни один готовый русскоязычный TTS-движок")

        started = time.monotonic()
        with self._lock, tempfile.TemporaryDirectory(prefix="leshy-tts-ru-", dir=self.temp_root) as tmp:
            out_path = Path(tmp) / "speech.wav"
            try:
                if engine in {"windows", "rhvoice"}:
                    voice = self._synthesize_windows(value, pref, preferred_voice, out_path)
                    backend = "rhvoice-sapi-ru" if engine == "rhvoice" else "windows-system-speech-ru"
                elif engine == "piper":
                    voice = self._synthesize_piper(value, pref, out_path)
                    backend = "piper-ru"
                elif engine == "espeak":
                    voice = self._synthesize_espeak(value, pref, out_path)
                    backend = "espeak-ng-ru"
                else:
                    raise RuntimeError("Неизвестный TTS backend")
                audio = out_path.read_bytes()
            except Exception as exc:
                self._last_error = str(exc)
                problem("voice_tts_failed", str(exc), engine=engine, language=self.LANGUAGE)
                raise RuntimeError(f"Не удалось озвучить русский ответ через {engine}: {exc}") from exc

        self._last_error = ""
        self._last_voice = voice
        self._last_backend = backend
        latency_ms = int((time.monotonic() - started) * 1000)
        event("voice_tts_ready", voice=voice, backend=backend, language=self.LANGUAGE, chars=len(value), audio_bytes=len(audio), latency_ms=latency_ms)
        return audio, {"voice": voice, "latency_ms": latency_ms, "backend": backend, "language": self.LANGUAGE}

    def status(self) -> dict[str, Any]:
        data = super().status()
        russian_voices: list[dict[str, Any]] = []
        for voice in list(data.get("voices") or []):
            engine = str(voice.get("engine") or "")
            language = str(voice.get("language") or "")
            if engine == "piper":
                if str(voice.get("id") or "").startswith("ru_RU-"):
                    russian_voices.append(voice)
            elif engine == "windows" and self._is_russian_language(language):
                russian_voices.append(voice)
        data["voices"] = russian_voices
        data["language"] = self.LANGUAGE
        data["language_locked"] = True
        data["language_note"] = "Сейчас голосовой ответ принудительно синтезируется на русском языке."

        have_ru_sapi = bool(self._russian_system_voices())
        for engine in list(data.get("engines") or []):
            if engine.get("id") == "windows":
                engine["available"] = bool(os.name == "nt" and self._powershell and have_ru_sapi)
                engine["description"] = "Штатный русский голос Windows/SAPI. Нерусские голоса скрыты."
            elif engine.get("id") == "rhvoice":
                engine["available"] = have_ru_sapi
                engine["description"] = "Русские SAPI-голоса; установленный RHVoice также подключается через SAPI."
            elif engine.get("id") == "piper":
                engine["description"] = "Локальный neural TTS. Леший использует только русские модели ru_RU."
            elif engine.get("id") == "espeak":
                engine["description"] = "Лёгкий резервный движок с принудительным русским голосом и UTF-8."
            elif engine.get("id") == "auto":
                engine["description"] = "Русский Piper при наличии подходящей модели, иначе русский Windows SAPI, затем eSpeak NG ru."
        return data


__all__ = ["RussianLocalVoiceTTS"]
