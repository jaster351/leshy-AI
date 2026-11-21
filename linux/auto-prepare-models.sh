#!/usr/bin/env bash
# Invoked by linux/build.sh after LeshyBrain has been restored.
# Never modify the active LLM backend or read weights from another cluster node.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRAIN="\${LESHY_BRAIN_DIR:-$ROOT/dist/Leshy/LeshyBrain}"
mkdir -p "$BRAIN/runtime/model-provision" "$BRAIN/logs"
LOCK="$BRAIN/runtime/model-provision/linux.lock"
LOG="$BRAIN/logs/model-auto-provision-linux.log"
STATE="$BRAIN/runtime/model-provision/linux-state.txt"
exec 9>"$LOCK"
flock -n 9 || exit 0
exec >>"$LOG" 2>&1
printf 'AUTO_MODEL_PROVISION_LINUX start=%s brain=%s\n' "$(date -Is)" "$BRAIN"
if [[ -f "$STATE" ]] && [[ "$(cat "$STATE")" == complete* ]]; then
  echo "PREVIOUS_INSTALL_COMPLETE"
  exit 0
fi
# Avoid repeated attempts on every build if HF or GitHub is unavailable.
if [[ -f "$STATE" ]] && [[ "$(cat "$STATE")" == failed* ]]; then
  since="$(find "$STATE" -mmin +240 -print)"
  if [[ -z "$since" ]]; then echo "BACKOFF_ACTIVE"; exit 0; fi
fi
status="complete"
if ! command -v inferssd >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/inferssd" ]]; then
  if command -v curl >/dev/null 2>&1; then
    installer="$BRAIN/runtime/model-provision/inferssd-install.sh"
    if curl -fLsS --max-time 120 \
      'https://github.com/wici-ai/inferssd/releases/download/261119.309/install.sh' \
      -o "$installer" && [[ -s "$installer" ]]; then
      # Official Linux installer builds a local native backend from source.
      bash "$installer" || status=failed
    else
      echo "INFERSSD_INSTALL_DOWNLOAD_UNAVAILABLE"
      status=failed
    fi
  else
    echo "INFERSSD_REQUIRES_CURL"
    status=failed
  fi
fi
# Do not auto-download an unsupported giant GGUF. InferSSD uses a verified
# local path only if one is selected for this particular server.
if [[ -n "\${LESHY_INFERSSD_GGUF_PATH:-}" ]]; then
  if [[ -f "$LESHY_INFERSSD_GGUF_PATH" ]]; then
    echo "INFERSSD_LOCAL_GGUF_PATH=$LESHY_INFERSSD_GGUF_PATH"
  else
    echo "INFERSSD_LOCAL_GGUF_MISSING; Qwen remains active"
  fi
fi
MODEL="$BRAIN/models/Inkling-2-Mini-original"
OFFLOAD="$BRAIN/offload/Inkling-2-Mini"
VENV="$BRAIN/runtime/inkling-original-venv"
mkdir -p "$MODEL" "$OFFLOAD"
if [[ ! -f "$MODEL/.leshy-original.json" ]]; then
  avail="$(df -Pk "$BRAIN" | awk 'NR==2 {print $4}')"
  if [[ ! "$avail" =~ ^[0-9]+$ ]] || (( avail < 35*1024*1024 )); then
    echo "INKLING_NO_SPACE: at least 35 GiB free required"
    status=failed
  elif command -v python3 >/dev/null 2>&1; then
    if [[ ! -x "$VENV/bin/python" ]]; then python3 -m venv "$VENV" || status=failed; fi
    if [[ -x "$VENV/bin/python" ]]; then
      export PYTHONPATH="$ROOT/src"
      export LESHY_VERSION_FILE="$ROOT/VERSION"
      export LESHY_BRAIN_DIR="$BRAIN"
      export LESHY_INKLING_MODEL_DIR="$MODEL"
      export LESHY_SSD_OFFLOAD_DIR="$OFFLOAD"
      export PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=3
      "$VENV/bin/python" -m pip install 'huggingface_hub>=1.31,<2' 'hf_xet>=1.8,<2' &&
        "$VENV/bin/python" -m leshy.inkling_original download --brain-dir "$BRAIN" || status=failed
    fi
  else
    echo "INKLING_PYTHON3_NOT_FOUND"
    status=failed
  fi
fi
echo "$status $(date -Is)" > "$STATE"
printf 'AUTO_MODEL_PROVISION_LINUX finished=%s status=%s\n' "$(date -Is)" "$status"
exit 0
