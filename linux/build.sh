#!/usr/bin/env bash
# Linux one-dir сборка. Данные LeshyBrain не перезаписываются PyInstaller.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
PY="$ROOT/.venv/bin/python"
"$PY" -m pip install -e '.[native,test,build]' 'diskcache>=5.6,<6' 'jinja2>=3.1,<4'
"$PY" -m pip check
"$PY" -c 'from llama_cpp import Llama; import diskcache, jinja2; print("CPU LLM runtime OK")'
"$PY" -m compileall -q src
"$PY" -m pytest -q

# Критически важно: PyInstaller --clean удаляет dist/Leshy.
# Переносим память, модели и профили в безопасный временный каталог.
BRAIN="$ROOT/dist/Leshy/LeshyBrain"
SAFE_ROOT=""
restore_brain() {
  if [[ -n "$SAFE_ROOT" && -d "$SAFE_ROOT/LeshyBrain" ]]; then
    mkdir -p "$(dirname "$BRAIN")"
    if [[ -e "$BRAIN" ]]; then
      echo "Ошибка: папка LeshyBrain уже существует. Резервные данные: $SAFE_ROOT/LeshyBrain" >&2
      return 1
    fi
    mv "$SAFE_ROOT/LeshyBrain" "$BRAIN"
    rmdir "$SAFE_ROOT" || true
  fi
}
if [[ -d "$BRAIN" ]]; then
  SAFE_ROOT="$(mktemp -d "$ROOT/.leshy-preserved-brain.XXXXXXXX")"
  mv "$BRAIN" "$SAFE_ROOT/LeshyBrain"
fi
trap restore_brain EXIT

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
  --hidden-import diskcache \
  --hidden-import jinja2 \
  --add-data 'web:web' \
  --add-data 'VERSION:.' \
  windows/launcher.py
BIN="$ROOT/dist/Leshy/Leshy"
test -x "$BIN"
"$BIN" --help >/dev/null
"$BIN" --check-native-runtime
restore_brain
trap - EXIT
printf '\nСборка готова: %s\nЗапуск: %s --no-browser\n' "$BIN" "$BIN"
