param(
  [string]$BrainDir = "",
  [switch]$Download,
  [switch]$InstallInference,
  [switch]$Activate
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if (-not $BrainDir) {
  $packaged = Join-Path $repo 'dist\Leshy\LeshyBrain'
  $BrainDir = if (Test-Path -LiteralPath $packaged -PathType Container) { $packaged } else { Join-Path $repo 'LeshyBrain' }
}
New-Item -ItemType Directory -Force -Path $BrainDir | Out-Null
$BrainDir = (Resolve-Path -LiteralPath $BrainDir).Path
$env:LESHY_BRAIN_DIR = $BrainDir

# Install the HF downloader in its own Python environment. Never upgrade global
# click/huggingface_hub: browser-use in the main Leshy installation pins click.
$venv = Join-Path $BrainDir 'runtime\inkling-original-venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  Write-Host ('Creating isolated Inkling Python environment: ' + $venv)
  & py -3.11 -m venv $venv
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Cannot create isolated Inkling Python 3.11 environment'
  }
}

# Original model and disk offload are stored next to the installed Leshy.exe.
$env:LESHY_INKLING_MODEL_DIR = Join-Path $BrainDir 'models\Inkling-2-Mini-original'
$env:LESHY_SSD_OFFLOAD_DIR = Join-Path $BrainDir 'offload\Inkling-2-Mini'
[Environment]::SetEnvironmentVariable('LESHY_INKLING_MODEL_DIR', $env:LESHY_INKLING_MODEL_DIR, 'User')
[Environment]::SetEnvironmentVariable('LESHY_SSD_OFFLOAD_DIR', $env:LESHY_SSD_OFFLOAD_DIR, 'User')
# The frozen Leshy sidecar will reuse exactly this isolated environment.
$env:LESHY_INKLING_PYTHON = $python
[Environment]::SetEnvironmentVariable('LESHY_INKLING_PYTHON', $python, 'User')

Write-Host ('Original model directory: ' + $env:LESHY_INKLING_MODEL_DIR)
Write-Host ('SSD offload directory: ' + $env:LESHY_SSD_OFFLOAD_DIR)
Write-Host ('Isolated Python: ' + $python)

# This runs inside the isolated venv; pip proxy is inherited from the
# model-provisioning worker rather than applied to the user's global session.
& $python -m pip install 'huggingface_hub>=1.31,<2' 'hf_xet>=1.8,<2'
if ($LASTEXITCODE -ne 0) { throw 'Hugging Face dependencies installation failed in isolated Python' }
# The sidecar needs only Leshy's source modules, not the entire leshy-ai package
# (which would falsely declare all GUI/server dependencies missing in this venv).
$sourceDir = Join-Path $repo 'src'
$env:PYTHONPATH = $sourceDir
$env:LESHY_INKLING_SOURCE_DIR = $sourceDir
[Environment]::SetEnvironmentVariable('LESHY_INKLING_SOURCE_DIR', $sourceDir, 'User')
if (-not (Test-Path -LiteralPath (Join-Path $sourceDir 'leshy\inkling_original.py') -PathType Leaf)) {
  throw 'Leshy Inkling Python source missing'
}
# Use the project VERSION from the source tree without installing leshy-ai.
$env:LESHY_VERSION_FILE = Join-Path $repo 'VERSION'

if ($InstallInference) {
  & $python -m pip install 'transformers>=5,<6' 'accelerate>=1,<2' 'torch>=2.5'
  if ($LASTEXITCODE -ne 0) { throw 'Optional Transformers inference dependencies installation failed' }
}

# The Python downloader tries DIRECT first and falls back to previously configured
# cyberhost HTTP CONNECT proxies only for external HF model downloads.
# Set LESHY_HF_PROXY_URL to override the first proxy candidate.
if ($Download) {
  & $python -m leshy.inkling_original download --brain-dir $BrainDir
} else {
  & $python -m leshy.inkling_original status --brain-dir $BrainDir
}
if ($LASTEXITCODE -ne 0) { throw 'Inkling original checkpoint preparation failed (see download errors above)' }

if ($Activate) {
  $stateText = & $python -m leshy.inkling_original status --brain-dir $BrainDir
  if ($LASTEXITCODE -ne 0) { throw 'Cannot verify checkpoint before activation' }
  $state = ($stateText | Out-String | ConvertFrom-Json)
  if ($state.state -ne 'downloaded') { throw 'Cannot activate original Inkling: checkpoint download is incomplete' }
  [Environment]::SetEnvironmentVariable('LESHY_LLM_BACKEND', 'inkling-original', 'User')
  Write-Host 'Original backend configured for next launch. Existing GGUF fallback remains available.'
  Write-Warning 'Activation does not guarantee original NVFP4/MXFP8 inference on RTX 3070 Laptop.'
} else {
  Write-Host 'Current active Qwen and its reasoning settings have not been changed.'
}
