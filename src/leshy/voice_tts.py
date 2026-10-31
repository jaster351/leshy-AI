from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

from .diagnostics import event, problem


class LocalVoiceTTS:
    """Локальная озвучка ответа без браузерного Web Speech API.

    На Windows используется встроенный System.Speech/SAPI через отдельный
    PowerShell-процесс. Текст передаётся через временный UTF-8 файл, поэтому не
    попадает в командную строку и не интерпретируется как shell-код.
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.root = self.brain_dir / "voice"
        self.temp_root = self.root / "tmp"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_error = ""
        self._last_voice = ""
        self._powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("powershell") or shutil.which("pwsh")

    def _script(self) -> str:
        return r'''param(
  [Parameter(Mandatory=$true)][string]$TextPath,
  [Parameter(Mandatory=$true)][string]$OutPath,
  [string]$PreferredVoice = ""
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  if ($PreferredVoice) {
    try { $synth.SelectVoice($PreferredVoice) } catch { }
  } else {
    $voices = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo })
    $ru = $voices | Where-Object { $_.Culture.Name -like 'ru-*' } | Select-Object -First 1
    if ($ru) { $synth.SelectVoice($ru.Name) }
  }
  $text = [System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8)
  $synth.Rate = 0
  $synth.Volume = 100
  $synth.SetOutputToWaveFile($OutPath)
  $synth.Speak($text)
  $voice = $synth.Voice.Name
  [Console]::Out.Write($voice)
} finally {
  try { $synth.Dispose() } catch { }
}
'''

    def available(self) -> bool:
        return os.name == "nt" and bool(self._powershell)

    def synthesize(self, text: str, *, preferred_voice: str = "") -> tuple[bytes, dict[str, Any]]:
        value = str(text or "").strip()
        if not value:
            raise ValueError("Текст для озвучивания пуст")
        if len(value) > 12000:
            value = value[:12000]
        if not self.available():
            raise RuntimeError("Локальная озвучка System.Speech доступна только на Windows с PowerShell")

        started = time.monotonic()
        with self._lock, tempfile.TemporaryDirectory(prefix="leshy-tts-", dir=self.temp_root) as tmp:
            folder = Path(tmp)
            text_path = folder / "speech.txt"
            out_path = folder / "speech.wav"
            script_path = folder / "speak.ps1"
            text_path.write_text(value, encoding="utf-8")
            script_path.write_text(self._script(), encoding="utf-8")
            cmd = [
                str(self._powershell),
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", str(script_path),
                "-TextPath", str(text_path),
                "-OutPath", str(out_path),
            ]
            if preferred_voice.strip():
                cmd += ["-PreferredVoice", preferred_voice.strip()]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    creationflags=(0x08000000 if os.name == "nt" else 0),
                )
            except Exception as exc:
                self._last_error = str(exc)
                problem("voice_tts_failed", str(exc))
                raise RuntimeError(f"Не удалось запустить локальную озвучку: {exc}") from exc
            if proc.returncode != 0 or not out_path.is_file():
                detail = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
                self._last_error = detail
                problem("voice_tts_failed", detail)
                raise RuntimeError("Локальная озвучка Windows не выполнена: " + detail[:600])
            audio = out_path.read_bytes()
            voice = (proc.stdout or "").strip()
            self._last_voice = voice
            self._last_error = ""
            latency_ms = int((time.monotonic() - started) * 1000)
            event("voice_tts_ready", voice=voice, chars=len(value), audio_bytes=len(audio), latency_ms=latency_ms)
            return audio, {"voice": voice, "latency_ms": latency_ms, "backend": "windows-system-speech"}

    def status(self) -> dict[str, Any]:
        return {
            "backend": "windows-system-speech",
            "available": self.available(),
            "uses_browser_speech_api": False,
            "last_voice": self._last_voice,
            "last_error": self._last_error,
        }


__all__ = ["LocalVoiceTTS"]
