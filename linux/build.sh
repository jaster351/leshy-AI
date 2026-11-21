#!/usr/bin/env bash
# Build Linux Leshy into an independent staging folder, then publish atomically
# enough to preserve the live LeshyBrain and its active NVMe downloads.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

AUTO="$ROOT/.autobuild"
mkdir -p "$AUTO"
# Prevent overlapping Linux builds; model preparation uses its own lock.
exec 8>"$AUTO/linux-build.lock"
if ! flock -n 8; then
  echo "Linux build already in progress; skipping concurrent build." >&2
  exit 2
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
PY="$ROOT/.venv/bin/python"
"$PY" -m pip install -e '.[native,test,build]' 'diskcache>=5.6,<6' 'jinja2>=3.1,<4'
"$PY" -m pip check
"$PY" -c 'from llama_cpp import Llama; import diskcache, jinja2; print("CPU LLM runtime OK")'
"$PY" -m compileall -q src
"$PY" -m pytest -q

# Never point PyInstaller at dist/Leshy. Previous builds moved the whole
# LeshyBrain, racing against the independent model/downloader worker.
STAGE="$AUTO/linux-package-dist"
WORK="$AUTO/linux-pyinstaller-work"
LIVE="$ROOT/dist/Leshy"
BRAIN="$LIVE/LeshyBrain"
BACKUP="$AUTO/linux-live-backup"
if [[ -e "$BACKUP" ]]; then
  echo "Unresolved previous Linux publish backup at $BACKUP; refusing to overwrite." >&2
  exit 1
fi
rm -rf -- "$STAGE" "$WORK"
mkdir -p "$STAGE" "$WORK"

"$PY" -m PyInstaller --noconfirm --clean --onedir --console \
  --name Leshy \
  --distpath "$STAGE" \
  --workpath "$WORK" \
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

PACKAGE="$STAGE/Leshy"
STAGED_BIN="$PACKAGE/Leshy"
test -x "$STAGED_BIN"
if [[ -e "$PACKAGE/LeshyBrain" || -L "$PACKAGE/LeshyBrain" ]]; then
  echo "Unexpected LeshyBrain in staging package; refusing to publish." >&2
  exit 1
fi
"$STAGED_BIN" --help >/dev/null
"$STAGED_BIN" --check-native-runtime

mkdir -p "$LIVE" "$BACKUP"
# The existing LeshyBrain is NEVER renamed, copied, removed or included in
# rollback. Open model files and the running download keep their stable paths.
published=0
publish_phase=moving
recover_live_program() {
  if (( published != 0 )); then return; fi
  echo "Linux publication failed: restoring previous program; LeshyBrain untouched." >&2
  # If moving an old file failed, leave every unmoved original untouched.
  # Remove new staged files only after the copy phase has actually begun.
  if [[ "$publish_phase" == copying ]]; then
    for entry in "$LIVE"/* "$LIVE"/.[!.]* "$LIVE"/..?*; do
      [[ -e "$entry" || -L "$entry" ]] || continue
      [[ "$(basename "$entry")" == "LeshyBrain" ]] && continue
      rm -rf -- "$entry" || true
    done
  fi
  local restore_failed=0
  for entry in "$BACKUP"/* "$BACKUP"/.[!.]* "$BACKUP"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    mv -- "$entry" "$LIVE/" || restore_failed=1
  done
  if (( restore_failed == 0 )); then
    rmdir "$BACKUP" 2>/dev/null || true
  else
    echo "Rollback incomplete. Previous program retained in $BACKUP." >&2
  fi
}
trap recover_live_program EXIT

for entry in "$LIVE"/* "$LIVE"/.[!.]* "$LIVE"/..?*; do
  [[ -e "$entry" || -L "$entry" ]] || continue
  [[ "$(basename "$entry")" == "LeshyBrain" ]] && continue
  mv -- "$entry" "$BACKUP/"
done
publish_phase=copying
cp -a "$PACKAGE"/. "$LIVE/"
test -x "$LIVE/Leshy"
published=1
trap - EXIT
rm -rf -- "$BACKUP"

# Model installation and downloads are explicitly independent of the code
# release and may continue through the next build without losing their path.
# The child MUST NOT inherit the Linux build lock (fd 8).
if [[ -f "$ROOT/linux/auto-prepare-models.sh" ]]; then
  nohup bash "$ROOT/linux/auto-prepare-models.sh" \
    >/dev/null 2>&1 </dev/null 8>&- &
  printf 'Model provisioning runs separately: %s\n' "$BRAIN/logs/model-auto-provision-linux.log"
fi
printf '\nСборка готова: %s\nЗапуск: %s --no-browser\n' "$LIVE/Leshy" "$LIVE/Leshy"
