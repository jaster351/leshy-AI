$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))

$version = (Get-Content VERSION -Raw).Trim()
if (-not ($version -match '^\d+\.\d+\.\d+')) { throw "Invalid VERSION: $version" }

python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install "pyinstaller>=6.11,<7"
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean --onedir --console `
  --name Leshy `
  --paths src `
  --collect-all llama_cpp `
  --collect-all ddgs `
  --add-data "web;web" `
  --add-data "VERSION;." `
  windows/launcher.py

if (-not (Test-Path 'dist/Leshy/Leshy.exe')) { throw 'PyInstaller did not create dist/Leshy/Leshy.exe' }

$env:LESHY_LLM_BACKEND = 'echo'
$smokeRoot = (Resolve-Path 'dist/Leshy').Path
$smokeBrain = Join-Path $smokeRoot 'LeshyBrain'
Remove-Item -Recurse -Force $smokeBrain -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath (Resolve-Path 'dist/Leshy/Leshy.exe') -ArgumentList '--no-browser','--host','127.0.0.1','--port','18765' -PassThru -WindowStyle Hidden
try {
  $ok = $false
  foreach ($i in 1..30) {
    Start-Sleep -Milliseconds 500
    try {
      $status = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/v1/status' -TimeoutSec 2
      if ($status.version -eq $version) { $ok = $true; break }
    } catch { }
  }
  if (-not $ok) { throw 'Frozen smoke-test did not receive /v1/status' }

  $actualBrain = [System.IO.Path]::GetFullPath([string]$status.brain_dir).TrimEnd('\')
  $expectedBrain = [System.IO.Path]::GetFullPath($smokeBrain).TrimEnd('\')
  if ($actualBrain -ne $expectedBrain) {
    throw "Frozen app uses wrong data root. Expected: $expectedBrain ; Actual: $actualBrain"
  }

  $reply = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:18765/v1/chat' -ContentType 'application/json' -Body '{"message":"smoke","owner_id":"ci","session_id":"ci-smoke"}'
  if (-not $reply.answer) { throw 'Frozen smoke-test chat returned an empty answer' }
} finally {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 300
  Remove-Item -Recurse -Force $smokeBrain -ErrorAction SilentlyContinue
}

$isccPath = $null
$cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($cmd) { $isccPath = $cmd.Source }
if (-not $isccPath) {
  $candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { $isccPath = $candidate; break }
  }
}
if (-not $isccPath) { throw 'Inno Setup 6 (ISCC.exe) was not found' }
& $isccPath "/DMyAppVersion=$version" 'windows/installer.iss'

$installer = "dist/LeshySetup-$version.exe"
if (-not (Test-Path $installer)) { throw "Installer was not created: $installer" }
Write-Host "READY: $installer"
