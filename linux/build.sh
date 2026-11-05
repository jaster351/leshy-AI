#!/usr/bin/env bash
# Ubuntu / CentOS Stream: локальная Linux-сборка Лешего (PyInstaller onedir).
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo "Не найден .venv/bin/python. Сначала выполните команды установки из README." >&2
  exit 2
fi
PY="$ROOT/.venv/bin/python"
"$PY" -m compileall -q src
"$PY" -m pytest -q
"$PY" -m PyInstaller --noconfirm --clean --onedir --console \
  --name Leshy \
  --paths src \
  --collect-all llama_cpp \
  --collect-all ddgs \
  --collect-all tzdata \
  --collect-all faster_whisper \
  --collect-all ctranslate2 \
  --collect-all av \
  --collect-all tokenizers \
  --collect-all huggingface_hub \
  --collect-all hf_xet \
  --collect-all playwright \
  --collect-all browser_use \
  --exclude-module browser_use.llm.oci_raw \
  --exclude-module oci \
  --collect-all markitdown \
  --collect-all mcp \
  --collect-all mss \
  --collect-all pyautogui \
  --collect-all pypdf \
  --add-data "web:web" \
  --add-data "VERSION:." \
  windows/launcher.py
BIN="$ROOT/dist/Leshy/Leshy"
if [[ ! -x "$BIN" ]]; then
  echo "PyInstaller не создал исполняемый файл $BIN" >&2
  exit 1
fi
"$BIN" --help >/dev/null
echo "Linux-сборка готова: $BIN"
echo "Запуск: $BIN --no-browser"
echo "Проверка на Linux не подтверждена до выполнения скрипта на целевой машине."
