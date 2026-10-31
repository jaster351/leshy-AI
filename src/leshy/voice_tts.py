from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib import request
import zipfile

from .diagnostics import event, problem
from .voice_preferences import VoicePreferenceStore


_PIPER_RELEASE_URL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
_PIPER_VOICE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
_ESPEAK_WINDOWS_URL = "https://github.com/espeak-ng/espeak-ng/releases/download/1.51/espeak-ng-X64.msi"

_PIPER_VOICES = {
    "ru_RU-denis-medium": {"name": "Денис", "gender": "male", "path": "ru/ru_RU/denis/medium", "natural": True},
    "ru_RU-dmitri-medium": {"name": "Дмитрий", "gender": "male", "path": "ru/ru_RU/dmitri/medium", "natural": True},
    "ru_RU-ruslan-medium": {"name": "Руслан", "gender": "male", "path": "ru/ru_RU/ruslan/medium", "natural": True},
    "ru_RU-irina-medium": {"name": "Ирина", "gender": "female", "path": "ru/ru_RU/irina/medium", "natural": True},
}

_PROFILE_LABELS = {
    "natural": "Естественный",
    "heroic": "Киногерой · уверенный",
    "noir": "Нуар · спокойный",
    "android": "Андроид · ровный",
    "villain": "Кинозлодей · низкий и медленный",
    "narrator": "Рассказчик · выразительный",
}
_PROFILE_RATE = {"natural": 1.0, "heroic": 0.96, "noir": 0.90, "android": 1.04, "villain": 0.86, "narrator": 0.94}


class LocalVoiceTTS:
    """Единый локальный TTS-менеджер Лешего.

    Движки не держатся в памяти постоянно: они запускаются только во время
    озвучивания, поэтому не конкурируют с LLM за RAM/GPU. Piper хранится только
    в LeshyBrain/voice и после установки работает офлайн.
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.root = self.brain_dir / "voice"
        self.temp_root = self.root / "tmp"
        self.engines_root = self.root / "engines"
        self.voices_root = self.root / "voices"
        self.downloads_root = self.root / "downloads"
        for folder in (self.temp_root, self.engines_root, self.voices_root, self.downloads_root):
            folder.mkdir(parents=True, exist_ok=True)
        self.preferences = VoicePreferenceStore(self.brain_dir)
        self._lock = threading.RLock()
        self._last_error = ""
        self._last_voice = ""
        self._last_backend = ""
        self._powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("powershell") or shutil.which("pwsh")

    def _system_script(self) -> str:
        return r'''param(
  [Parameter(Mandatory=$true)][string]$TextPath,
  [Parameter(Mandatory=$true)][string]$OutPath,
  [string]$PreferredVoice = "",
  [string]$Gender = "Male",
  [int]$Rate = 0
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voices = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo })
  if ($PreferredVoice) {
    try { $synth.SelectVoice($PreferredVoice) } catch { }
  } else {
    $preferred = $voices | Where-Object { $_.Culture.Name -like 'ru-*' -and $_.Gender.ToString() -eq $Gender } | Select-Object -First 1
    if (-not $preferred) { $preferred = $voices | Where-Object { $_.Gender.ToString() -eq $Gender } | Select-Object -First 1 }
    if (-not $preferred) { $preferred = $voices | Where-Object { $_.Culture.Name -like 'ru-*' } | Select-Object -First 1 }
    if ($preferred) { $synth.SelectVoice($preferred.Name) }
  }
  $text = [System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8)
  $synth.Rate = [Math]::Max(-10,[Math]::Min(10,$Rate))
  $synth.Volume = 100
  $synth.SetOutputToWaveFile($OutPath)
  $synth.Speak($text)
  [Console]::Out.Write($synth.Voice.Name)
} finally {
  try { $synth.Dispose() } catch { }
}
'''

    def _voice_list_script(self) -> str:
        return r'''$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $items = @($synth.GetInstalledVoices() | ForEach-Object {
    $v=$_.VoiceInfo
    [PSCustomObject]@{id=$v.Name;name=$v.Name;gender=$v.Gender.ToString().ToLowerInvariant();language=$v.Culture.Name}
  })
  $items | ConvertTo-Json -Compress
} finally { try { $synth.Dispose() } catch {} }
'''

    def _run_powershell(self, script: str, args: list[str] | None = None, *, timeout: int = 30) -> subprocess.CompletedProcess:
        if not self._powershell:
            raise RuntimeError("PowerShell не найден")
        with tempfile.TemporaryDirectory(prefix="leshy-tts-ps-", dir=self.temp_root) as tmp:
            script_path = Path(tmp) / "script.ps1"
            script_path.write_text(script, encoding="utf-8")
            cmd = [str(self._powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)] + list(args or [])
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=(0x08000000 if os.name == "nt" else 0))

    def _system_voices(self) -> list[dict[str, str]]:
        if os.name != "nt" or not self._powershell:
            return []
        try:
            proc = self._run_powershell(self._voice_list_script(), timeout=20)
            if proc.returncode != 0:
                return []
            raw = json.loads((proc.stdout or "[]").strip() or "[]")
            if isinstance(raw, dict):
                raw = [raw]
            return [dict(item) for item in raw if isinstance(item, dict)]
        except Exception:
            return []

    def _piper_executable(self) -> Path | None:
        direct = self.engines_root / "piper" / "piper.exe"
        if direct.is_file():
            return direct
        for candidate in (self.engines_root / "piper").rglob("piper.exe") if (self.engines_root / "piper").exists() else ():
            return candidate
        found = shutil.which("piper") or shutil.which("piper.exe")
        return Path(found) if found else None

    def _espeak_executable(self) -> Path | None:
        for raw in (
            shutil.which("espeak-ng"), shutil.which("espeak-ng.exe"),
            r"C:\Program Files\eSpeak NG\espeak-ng.exe", r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe",
        ):
            if raw and Path(raw).is_file():
                return Path(raw)
        return None

    def _piper_voice_paths(self, voice_id: str) -> tuple[Path, Path]:
        target = self.voices_root / "piper" / voice_id
        return target / f"{voice_id}.onnx", target / f"{voice_id}.onnx.json"

    def _download(self, url: str, target: Path, *, timeout: int = 180) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".part")
        req = request.Request(url, headers={"User-Agent": "Leshy-AI/voice-engine-manager"})
        try:
            with request.urlopen(req, timeout=timeout) as response, temp.open("wb") as out:
                shutil.copyfileobj(response, out)
            os.replace(temp, target)
        finally:
            if temp.exists():
                try: temp.unlink()
                except OSError: pass

    def install(self, engine: str, *, voice_id: str = "") -> dict[str, Any]:
        engine = str(engine or "").strip().lower()
        with self._lock:
            if engine == "piper":
                exe = self._piper_executable()
                if exe is None:
                    archive = self.downloads_root / "piper_windows_amd64.zip"
                    self._download(_PIPER_RELEASE_URL, archive)
                    target = self.engines_root / "piper"
                    target.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(archive) as zf:
                        zf.extractall(target)
                    exe = self._piper_executable()
                    if exe is None:
                        raise RuntimeError("Piper скачан, но piper.exe в архиве не найден")
                selected = voice_id if voice_id in _PIPER_VOICES else self._default_piper_voice(self.preferences.load().effective_gender)
                self.install_voice(selected)
                event("voice_engine_installed", engine="piper", voice=selected)
                return self.status()
            if engine == "espeak":
                if os.name != "nt":
                    raise RuntimeError("Автоустановка eSpeak NG сейчас поддерживается только на Windows")
                if self._espeak_executable() is None:
                    msi = self.downloads_root / "espeak-ng-X64.msi"
                    self._download(_ESPEAK_WINDOWS_URL, msi)
                    subprocess.Popen(["msiexec.exe", "/i", str(msi)], creationflags=(0x08000000 if os.name == "nt" else 0))
                    return {**self.status(), "installer_started": True, "message": "Установщик eSpeak NG запущен. После завершения обновите список голосов."}
                return self.status()
            if engine == "rhvoice":
                return {**self.status(), "message": "RHVoice подключается через Windows SAPI. Если RHVoice уже установлен, его голос появится в списке системных голосов."}
            if engine in {"windows", "auto"}:
                return self.status()
            raise ValueError("Неизвестный голосовой движок")

    def install_voice(self, voice_id: str) -> dict[str, Any]:
        info = _PIPER_VOICES.get(str(voice_id or ""))
        if not info:
            raise ValueError("Неизвестный голос Piper")
        model, config = self._piper_voice_paths(voice_id)
        remote_dir = str(info["path"])
        if not model.is_file():
            self._download(f"{_PIPER_VOICE_BASE}/{remote_dir}/{voice_id}.onnx?download=true", model)
        if not config.is_file():
            self._download(f"{_PIPER_VOICE_BASE}/{remote_dir}/{voice_id}.onnx.json?download=true", config)
        event("voice_pack_installed", engine="piper", voice=voice_id)
        return self.status()

    @staticmethod
    def _default_piper_voice(gender: str) -> str:
        return "ru_RU-irina-medium" if gender == "female" else "ru_RU-denis-medium"

    def _effective_rate(self, pref) -> float:
        return max(0.70, min(1.30, float(pref.rate) * _PROFILE_RATE.get(pref.profile, 1.0)))

    def _synthesize_windows(self, value: str, pref, preferred_voice: str, out_path: Path) -> str:
        gender = "Female" if pref.effective_gender == "female" else "Male"
        rate = int(round((self._effective_rate(pref) - 1.0) * 10))
        with tempfile.TemporaryDirectory(prefix="leshy-system-tts-", dir=self.temp_root) as tmp:
            text_path = Path(tmp) / "speech.txt"
            script_path = Path(tmp) / "speak.ps1"
            text_path.write_text(value, encoding="utf-8")
            script_path.write_text(self._system_script(), encoding="utf-8")
            cmd = [str(self._powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), "-TextPath", str(text_path), "-OutPath", str(out_path), "-Gender", gender, "-Rate", str(rate)]
            voice = preferred_voice.strip() or pref.voice_id.strip()
            if voice:
                cmd += ["-PreferredVoice", voice]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, creationflags=(0x08000000 if os.name == "nt" else 0))
            if proc.returncode != 0 or not out_path.is_file():
                raise RuntimeError((proc.stderr or proc.stdout or f"exit={proc.returncode}").strip())
            return (proc.stdout or "").strip()

    def _synthesize_piper(self, value: str, pref, out_path: Path) -> str:
        exe = self._piper_executable()
        if exe is None:
            raise RuntimeError("Piper ещё не установлен. Нажмите «Скачать/подключить» в настройках голоса.")
        voice_id = pref.voice_id if pref.voice_id in _PIPER_VOICES else self._default_piper_voice(pref.effective_gender)
        model, config = self._piper_voice_paths(voice_id)
        if not model.is_file() or not config.is_file():
            raise RuntimeError(f"Голос {voice_id} не загружен. Нажмите «Скачать/подключить».")
        length_scale = 1.0 / self._effective_rate(pref)
        cmd = [str(exe), "--model", str(model), "--config", str(config), "--output_file", str(out_path), "--length_scale", f"{length_scale:.3f}"]
        proc = subprocess.run(cmd, input=value, capture_output=True, text=True, timeout=120, creationflags=(0x08000000 if os.name == "nt" else 0))
        if proc.returncode != 0 or not out_path.is_file():
            raise RuntimeError((proc.stderr or proc.stdout or f"exit={proc.returncode}").strip())
        return voice_id

    def _synthesize_espeak(self, value: str, pref, out_path: Path) -> str:
        exe = self._espeak_executable()
        if exe is None:
            raise RuntimeError("eSpeak NG не установлен")
        variant = "f3" if pref.effective_gender == "female" else "m3"
        speed = max(100, min(260, int(175 * self._effective_rate(pref))))
        with tempfile.TemporaryDirectory(prefix="leshy-espeak-", dir=self.temp_root) as tmp:
            text_path = Path(tmp) / "speech.txt"
            text_path.write_text(value, encoding="utf-8")
            proc = subprocess.run([str(exe), "-v", f"ru+{variant}", "-s", str(speed), "-f", str(text_path), "-w", str(out_path)], capture_output=True, text=True, timeout=90, creationflags=(0x08000000 if os.name == "nt" else 0))
            if proc.returncode != 0 or not out_path.is_file():
                raise RuntimeError((proc.stderr or proc.stdout or f"exit={proc.returncode}").strip())
        return f"eSpeak NG ru+{variant}"

    def available(self) -> bool:
        status = self.status()
        return any(bool(item.get("available")) for item in status["engines"] if item["id"] != "auto")

    def configure(self, **values: Any) -> dict[str, Any]:
        pref = self.preferences.save(
            values.get("selected_gender"),
            engine=values.get("engine"),
            voice_id=values.get("voice_id"),
            profile=values.get("profile"),
            rate=values.get("rate"),
        )
        event("voice_preferences_saved", **pref.as_dict())
        return self.status()

    def synthesize(self, text: str, *, preferred_voice: str = "") -> tuple[bytes, dict[str, Any]]:
        value = str(text or "").strip()
        if not value:
            raise ValueError("Текст для озвучивания пуст")
        if len(value) > 20000:
            value = value[:20000]
        pref = self.preferences.load()
        engine = pref.effective_engine
        if engine == "auto":
            engine = "piper" if self._piper_executable() else "windows"
        started = time.monotonic()
        with self._lock, tempfile.TemporaryDirectory(prefix="leshy-tts-", dir=self.temp_root) as tmp:
            out_path = Path(tmp) / "speech.wav"
            try:
                if engine in {"windows", "rhvoice"}:
                    if os.name != "nt" or not self._powershell:
                        raise RuntimeError("Windows System Speech недоступен")
                    voice = self._synthesize_windows(value, pref, preferred_voice, out_path)
                    backend = "rhvoice-sapi" if engine == "rhvoice" else "windows-system-speech"
                elif engine == "piper":
                    voice = self._synthesize_piper(value, pref, out_path)
                    backend = "piper"
                elif engine == "espeak":
                    voice = self._synthesize_espeak(value, pref, out_path)
                    backend = "espeak-ng"
                else:
                    raise RuntimeError("Неизвестный TTS backend")
                audio = out_path.read_bytes()
            except Exception as exc:
                self._last_error = str(exc)
                problem("voice_tts_failed", str(exc), engine=engine)
                raise RuntimeError(f"Не удалось озвучить ответ через {engine}: {exc}") from exc
        self._last_error = ""
        self._last_voice = voice
        self._last_backend = backend
        latency_ms = int((time.monotonic() - started) * 1000)
        event("voice_tts_ready", voice=voice, backend=backend, chars=len(value), audio_bytes=len(audio), latency_ms=latency_ms)
        return audio, {"voice": voice, "latency_ms": latency_ms, "backend": backend}

    def status(self) -> dict[str, Any]:
        pref = self.preferences.load()
        system_voices = self._system_voices()
        piper_installed = self._piper_executable() is not None
        piper_voices = []
        for voice_id, info in _PIPER_VOICES.items():
            model, config = self._piper_voice_paths(voice_id)
            piper_voices.append({
                "id": voice_id, "name": info["name"], "gender": info["gender"], "language": "ru-RU",
                "installed": model.is_file() and config.is_file(), "engine": "piper",
            })
        engines = [
            {"id": "auto", "name": "Авто · лучший доступный", "available": True, "installable": False, "open_source": False, "description": "Piper при наличии, иначе системный голос."},
            {"id": "piper", "name": "Piper · рекомендуемый", "available": piper_installed, "installable": True, "open_source": True, "license": "MIT/GPL (runtime depends on selected Piper distribution)", "description": "Естественный локальный neural TTS. После загрузки интернет не нужен."},
            {"id": "windows", "name": "Windows System Speech", "available": os.name == "nt" and bool(self._powershell), "installable": False, "open_source": False, "description": "Штатные голоса Windows/SAPI."},
            {"id": "rhvoice", "name": "RHVoice · Open Source", "available": bool(system_voices), "installable": False, "open_source": True, "license": "LGPL/GPL; лицензия отдельных голосов может отличаться", "description": "Использует установленный RHVoice через Windows SAPI."},
            {"id": "espeak", "name": "eSpeak NG · Open Source", "available": self._espeak_executable() is not None, "installable": os.name == "nt", "open_source": True, "license": "GPL-3.0", "description": "Очень лёгкий резервный движок; звучит менее естественно."},
        ]
        return {
            "backend": self._last_backend or pref.effective_engine,
            "available": self.available if False else True,
            "uses_browser_speech_api": False,
            "last_voice": self._last_voice,
            "last_error": self._last_error,
            "preference": pref.as_dict(),
            "engines": engines,
            "voices": piper_voices + [{**item, "engine": "windows", "installed": True} for item in system_voices],
            "profiles": [{"id": key, "name": label} for key, label in _PROFILE_LABELS.items()],
            "notes": {
                "cinematic": "Киношные профили меняют характер подачи, но не копируют голос конкретного актёра или персонажа.",
                "performance": "TTS запускается только на время озвучивания и не должен отбирать LLM-slot у текстовой генерации.",
            },
        }


__all__ = ["LocalVoiceTTS"]
