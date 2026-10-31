$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))

$version = (Get-Content VERSION -Raw).Trim()
if (-not ($version -match '^\d+\.\d+\.\d+')) { throw "Invalid VERSION: $version" }

$runtimeLogStage = Join-Path (Get-Location).Path '.autobuild\leshy-runtime-logs\current'
Remove-Item -Recurse -Force $runtimeLogStage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $runtimeLogStage | Out-Null

python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install "pyinstaller>=6.11,<7"
python -m pip install "llama-cpp-python>=0.3.27,<0.4" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

Write-Host 'Running unit tests...'
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }

$uiSource = Get-Content 'web/index.html' -Raw
foreach ($required in @('leshy.ui.mode','data-section="knowledge"','data-section="learning"','data-section="tasks"','data-section="tools"','data-section="system"','data-section="settings"','data-section="logs"')) {
  if ($uiSource -notlike "*$required*") { throw "Full Web UI contract is missing: $required" }
}

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
$smokeLogs = Join-Path $smokeBrain 'logs'
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

  $capabilities = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/v1/capabilities' -TimeoutSec 3
  if (-not $capabilities.items -or $capabilities.items.Count -lt 5) { throw 'Full dashboard capabilities endpoint failed' }

  $task = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:18765/v1/tasks' -ContentType 'application/json' -Body '{"owner_id":"ci","title":"smoke dashboard task","priority":1}'
  if (-not $task.id) { throw 'Dashboard task creation failed' }
  $tasks = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/v1/tasks?owner_id=ci' -TimeoutSec 3
  if (-not $tasks.items -or $tasks.items.Count -lt 1) { throw 'Dashboard tasks endpoint failed' }

  $overview = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/v1/overview?owner_id=ci' -TimeoutSec 3
  if (-not $overview.status.version) { throw 'Dashboard overview endpoint failed' }
} finally {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 300
  if (Test-Path $smokeLogs) {
    Get-ChildItem -LiteralPath $smokeLogs -File -ErrorAction SilentlyContinue | ForEach-Object {
      Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $runtimeLogStage $_.Name) -Force
    }
  }
  $meta = [ordered]@{
    version = $version
    captured_at = (Get-Date).ToString('o')
    source = 'dist/Leshy/LeshyBrain/logs'
  }
  $meta | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtimeLogStage 'build-metadata.json') -Encoding UTF8

  if ($env:GITHUB_ACTIONS -ne 'true') {
    $publisher = Join-Path $PSScriptRoot 'publish-build-logs.ps1'
    if (Test-Path $publisher) {
      try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $publisher -Source $runtimeLogStage -Version $version
        if ($LASTEXITCODE -ne 0) { Write-Warning "Leshy build log upload failed with exit code $LASTEXITCODE" }
      } catch {
        Write-Warning "Leshy build log upload failed: $($_.Exception.Message)"
      }
    }
  }

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
