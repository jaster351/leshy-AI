#!/usr/bin/env bash
# Invoked by linux/build.sh after LeshyBrain has been restored.
# Never modify the active LLM backend or read weights from another cluster node.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRAIN="${LESHY_BRAIN_DIR:-$ROOT/dist/Leshy/LeshyBrain}"
mkdir -p "$BRAIN/runtime/model-provision" "$BRAIN/logs"
LOCK="$BRAIN/runtime/model-provision/linux.lock"
LOG="$BRAIN/logs/model-auto-provision-linux.log"
STATE="$BRAIN/runtime/model-provision/linux-state.txt"
exec 9>"$LOCK"
flock -n 9 || exit 0
exec >>"$LOG" 2>&1
printf 'AUTO_MODEL_PROVISION_LINUX start=%s brain=%s\n' "$(date -Is)" "$BRAIN"
# Publish stage snapshots compatible with Windows and the web UI.
# Only local paths are written. A failed write is diagnostic, not a build failure.
write_stage() {
  local stage="$1" outcome="$2" details="$3"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$BRAIN" "$stage" "$outcome" "$details" <<'PY' || true
import datetime, json, os, pathlib, sys, tempfile
brain, step, outcome, details = sys.argv[1:]
root = pathlib.Path(brain) / "runtime" / "model-provision"
root.mkdir(parents=True, exist_ok=True)
payload = {
    "schema": 1, "step": step, "outcome": outcome,
    "details": details[:700],
    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
for name in ("state.json", step + ".json"):
    temp = root / (name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, root / name)
PY
  fi
}

# Versioned completion marker prevents an older Linux update from silently
# skipping the newly added MoE GGUF stage. Both manifests must remain present.
if [[ -f "$STATE" ]] && [[ "$(cat "$STATE")" == complete-v2* ]] &&
   [[ -f "$BRAIN/models/Inkling-2-Mini-original/.leshy-original.json" ]] &&
   [[ -f "$BRAIN/models/InferSSD-Qwen3.6-35B-A3B/.leshy-moe-gguf.json" ]]; then
  echo "PREVIOUS_MODEL_PROVISION_COMPLETE"
  exit 0
fi
# Avoid repeated attempts on every build if HF or GitHub is unavailable.
if [[ -f "$STATE" ]] && [[ "$(cat "$STATE")" == failed* ]]; then
  since="$(find "$STATE" -mmin +240 -print)"
  if [[ -z "$since" ]]; then echo "BACKOFF_ACTIVE"; exit 0; fi
fi
status="complete"
write_stage 'inferssd' 'running' 'Preparing native engine on the local server NVMe'
# Each Linux node installs the upstream engine on its own local NVMe next to
# LeshyBrain. Do not require administrator access or edit the user's shell.
export INFERSSD_HOME="${INFERSSD_HOME:-$BRAIN/runtime/inferssd}"
export INFERSSD_BIN_DIR="${INFERSSD_BIN_DIR:-$INFERSSD_HOME/bin}"
if ! command -v inferssd >/dev/null 2>&1 &&
   [[ ! -x "$HOME/.local/bin/inferssd" ]] &&
   [[ ! -x "$INFERSSD_BIN_DIR/inferssd" ]]; then
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
if [[ "$status" == complete ]]; then
  write_stage 'inferssd' 'installed' 'Native engine detected or successfully installed'
else
  write_stage 'inferssd' 'failed' 'Native engine installation failed, see local log'
fi
# Do not auto-download an unsupported giant GGUF. InferSSD uses a verified
# local path only if one is selected for this particular server.
if [[ -n "${LESHY_INFERSSD_GGUF_PATH:-}" ]]; then
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
write_stage 'inkling' 'running' 'Preparing original Inkling checkpoint on local NVMe'
if [[ ! -f "$MODEL/.leshy-original.json" ]]; then
  avail="$(df -Pk "$BRAIN" | awk 'NR==2 {print $4}')"
  if [[ ! "$avail" =~ ^[0-9]+$ ]] || (( avail < 45*1024*1024 )); then
    echo "INKLING_NO_SPACE: at least 45 GiB free required"
    status=failed
  elif command -v python3 >/dev/null 2>&1; then
    if [[ ! -x "$VENV/bin/python" ]]; then python3 -m venv "$VENV" || status=failed; fi
    if [[ -x "$VENV/bin/python" ]]; then
      export PYTHONPATH="$ROOT/src"
      export LESHY_VERSION_FILE="$ROOT/VERSION"
      export LESHY_BRAIN_DIR="$BRAIN"
      export LESHY_INKLING_MODEL_DIR="$MODEL"
      export LESHY_SSD_OFFLOAD_DIR="$OFFLOAD"
      export HF_HOME="$BRAIN/cache/huggingface"
      mkdir -p "$HF_HOME"
      export PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=3
      "$VENV/bin/python" -m pip install 'huggingface_hub>=1.31,<2' 'hf_xet>=1.8,<2' &&
        "$VENV/bin/python" -m leshy.inkling_original download --brain-dir "$BRAIN" || status=failed
    fi
  else
    echo "INKLING_PYTHON3_NOT_FOUND"
    status=failed
  fi
fi
if [[ -f "$MODEL/.leshy-original.json" ]]; then
  write_stage 'inkling' 'downloaded' 'Original checkpoint manifest is present; see model storage health for validation'
else
  write_stage 'inkling' 'failed' 'Original checkpoint missing or incomplete; see local log'
fi
# Independently provision an officially tested GGUF MoE (22 GiB) on this
# node's local NVMe. The original Inkling weights remain a separate format.
# No backend switch is made until successful native inference on that node.
export PYTHONPATH="$ROOT/src"
export LESHY_VERSION_FILE="$ROOT/VERSION"
export LESHY_BRAIN_DIR="$BRAIN"
export HF_HOME="$BRAIN/cache/huggingface"
export PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=3
mkdir -p "$HF_HOME"
# Legacy machines might already have original checkpoint files without the
# Python environment; create the isolated venv for GGUF staging as well.
if [[ ! -x "$VENV/bin/python" ]] && command -v python3 >/dev/null 2>&1; then
  python3 -m venv "$VENV" || status=failed
fi
write_stage 'moe' 'running' 'Preparing tested local InferSSD MoE GGUF'
MOE_ROOT="${LESHY_INFERSSD_MODEL_DIR:-$BRAIN/models/InferSSD-Qwen3.6-35B-A3B}"
mkdir -p "$MOE_ROOT"
# Do not demand another 45 GiB if the selected GGUF is already complete.
if [[ -x "$VENV/bin/python" ]] &&
   "$VENV/bin/python" -m leshy.moe_model_download status --brain-dir "$BRAIN" |
     grep -q '"state": "downloaded"'; then
  echo "INFERSSD_MOE_PRESENT"
else
  avail_moe="$(df -Pk "$MOE_ROOT" | awk 'NR==2 {print $4}')"
  if [[ ! "$avail_moe" =~ ^[0-9]+$ ]] || (( avail_moe < 45*1024*1024 )); then
    echo "INFERSSD_MOE_NO_SPACE: at least 45 GiB free required for model and HF cache"
    status=failed
  elif [[ -x "$VENV/bin/python" ]]; then
    export HF_HOME="$MOE_ROOT/cache/huggingface"
    mkdir -p "$HF_HOME"
    "$VENV/bin/python" -m pip install 'huggingface_hub>=1.31,<2' 'hf_xet>=1.8,<2' &&
      "$VENV/bin/python" -m leshy.moe_model_download download --brain-dir "$BRAIN" || status=failed
  else
    echo "INFERSSD_MOE_VENV_NOT_READY"
    status=failed
  fi
fi
if [[ "$status" == complete ]]; then status=complete-v2; fi
if [[ -f "$MOE_ROOT/.leshy-moe-gguf.json" ]]; then
  write_stage 'moe' 'downloaded' 'MoE GGUF manifest is present; see model storage health for validation'
else
  write_stage 'moe' 'failed' 'MoE GGUF missing or incomplete; see local log'
fi
# Write the same read-only report as Windows, even when model downloads
# are temporarily unavailable. No model is launched by this report.
if [[ -x "$VENV/bin/python" ]]; then
  export PYTHONPATH="$ROOT/src"
  export LESHY_VERSION_FILE="$ROOT/VERSION"
  "$VENV/bin/python" -m leshy.model_storage_health \
      --brain-dir "$BRAIN" \
      --output "$BRAIN/runtime/model-provision/storage-health.json" ||
      echo "MODEL_STORAGE_HEALTH_UNAVAILABLE"
fi
echo "$status $(date -Is)" > "$STATE"
printf 'AUTO_MODEL_PROVISION_LINUX finished=%s status=%s\n' "$(date -Is)" "$status"
exit 0
