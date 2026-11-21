param(
  [string]$SsdRoot = "",
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
$BrainDir = (Resolve-Path $BrainDir).Path
$env:LESHY_BRAIN_DIR = $BrainDir
$env:PYTHONPATH = (Join-Path $repo 'src') + [IO.Path]::PathSeparator + $env:PYTHONPATH

if ($SsdRoot) {
  New-Item -ItemType Directory -Force -Path $SsdRoot | Out-Null
  $SsdRoot = (Resolve-Path $SsdRoot).Path
  $env:LESHY_INKLING_MODEL_DIR = Join-Path $SsdRoot 'LeshyModels\Inkling-2-Mini-original'
  $env:LESHY_SSD_OFFLOAD_DIR = Join-Path $SsdRoot 'LeshyOffload\Inkling-2-Mini'
  [Environment]::SetEnvironmentVariable('LESHY_INKLING_MODEL_DIR', $env:LESHY_INKLING_MODEL_DIR, 'User')
  [Environment]::SetEnvironmentVariable('LESHY_SSD_OFFLOAD_DIR', $env:LESHY_SSD_OFFLOAD_DIR, 'User')
}
Write-Host ('Original model directory: ' + $env:LESHY_INKLING_MODEL_DIR)
Write-Host ('SSD offload directory: ' + $env:LESHY_SSD_OFFLOAD_DIR)

& py -3.11 -m pip install --upgrade 'huggingface_hub>=0.36,<2' 'hf_xet>=1.1,<2'
if ($LASTEXITCODE -ne 0) { throw 'Hugging Face dependencies installation failed' }
# The frozen Windows Leshy.exe uses this isolated Python runtime for original weights.
& py -3.11 -m pip install --no-deps -e $repo
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Leshy original checkpoint sidecar' }

if ($InstallInference) {
  & py -3.11 -m pip install --upgrade 'transformers>=5' 'accelerate>=1' 'torch'
  if ($LASTEXITCODE -ne 0) { throw 'Optional Transformers inference dependencies installation failed' }
}
if ($Download) {
  & py -3.11 -m leshy.inkling_original download --brain-dir $BrainDir
} else {
  & py -3.11 -m leshy.inkling_original status --brain-dir $BrainDir
}
if ($LASTEXITCODE -ne 0) { throw 'Inkling original checkpoint preparation failed' }

if ($Activate) {
  $stateText = & py -3.11 -m leshy.inkling_original status --brain-dir $BrainDir
  if ($LASTEXITCODE -ne 0) { throw 'Cannot verify checkpoint before activation' }
  $state = ($stateText | Out-String | ConvertFrom-Json)
  if ($state.state -ne 'downloaded') { throw 'Cannot activate original Inkling: checkpoint download is incomplete' }
  [Environment]::SetEnvironmentVariable('LESHY_LLM_BACKEND', 'inkling-original', 'User')
  Write-Host 'Opt-in backend configured for the next Leshy launch. Existing GGUF fallback is preserved.'
  Write-Warning 'Activation alone does not guarantee the original model runs on the RTX 3070 Laptop.'
} else {
  Write-Host 'Existing active model not changed.'
}
