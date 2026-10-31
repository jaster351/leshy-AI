$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))

$version = (Get-Content VERSION -Raw).Trim()
if (-not ($version -match '^\d+\.\d+\.\d+')) { throw "Некорректный VERSION: $version" }

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

if (-not (Test-Path 'dist/Leshy/Leshy.exe')) { throw 'PyInstaller не создал dist/Leshy/Leshy.exe' }

$env:LESHY_LLM_BACKEND = 'echo'
$env:LESHY_BRAIN_DIR = (Join-Path $env:TEMP 'LeshySmokeBrain')
Remove-Item -Recurse -Force $env:LESHY_BRAIN_DIR -ErrorAction SilentlyContinue
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
  if (-not $ok) { throw 'Frozen smoke-test не получил /v1/status' }
  $reply = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:18765/v1/chat' -ContentType 'application/json' -Body '{"message":"smoke","owner_id":"ci","session_id":"ci-smoke"}'
  if (-not ($reply.answer -like 'Эхо:*')) { throw 'Frozen smoke-test чата не прошёл' }
} finally {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

$isccPath = $null
$cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($cmd) { $isccPath = $cmd.Source }
if (-not $isccPath) {
  $candidate = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'
  if (Test-Path $candidate) { $isccPath = $candidate }
}
if (-not $isccPath) { throw 'Не найден Inno Setup 6 (ISCC.exe)' }
& $isccPath "/DMyAppVersion=$version" 'windows/installer.iss'

$installer = "dist/LeshySetup-$version.exe"
if (-not (Test-Path $installer)) { throw "Не создан $installer" }
Write-Host "READY: $installer"
