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
# Устанавливаем все зависимости CPU-LLM перед созданием frozen package.
# Ранее llama-cpp-python ставили с --no-deps; diskcache мог отсутствовать.
"$PY" -m pip install -e '.[native,test,build]'
"$PY" -m pip check
"$PY" -c 'from llama_cpp import Llama; import diskcache; import jinja2; print("CPU GGUF runtime: import OK")'
# Поставить системные библиотеки Chromium на Ubuntu/Debian и загрузить браузер
# в Playwright cache. Работает и на сервере без X11/Wayland.
if [[ "${LESHY_SKIP_BROWSER_INSTALL:-0}" != "1" ]]; then
  if [[ -r /etc/os-release ]] && grep -Eq '^ID="?((ubuntu)|(debian))"?
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
# Импорт из .venv недостаточен: проверяем само собранное приложение.
"$BIN" --check-native-runtime
echo "Linux-сборка готова: $BIN"
echo "Запуск: $BIN --no-browser"
echo "Проверка на Linux не подтверждена до выполнения скрипта на целевой машине."
 /etc/os-release; then
    "$PY" -m playwright install --with-deps chromium
  else
    "$PY" -m playwright install chromium
  fi
  "$PY" -c 'from leshy.system_browser import ensure_system_browser; print("Browser:", ensure_system_browser(auto_install=False).executable)'
fi
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
# Импорт из .venv недостаточен: проверяем само собранное приложение.
"$BIN" --check-native-runtime
echo "Linux-сборка готова: $BIN"
echo "Запуск: $BIN --no-browser"
echo "Проверка на Linux не подтверждена до выполнения скрипта на целевой машине."
